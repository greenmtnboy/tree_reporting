#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Maple Ridge's heritage register, from the City of Maple Ridge.

Source: two layers of `DataCatalog/Recreation/MapServer` on the City's own
ArcGIS Server, both catalogued on the City's Open Government portal under the
Open Government Licence - Maple Ridge v2.0
(https://opengov.mapleridge.ca/pages/open-government-licence):

  * `Heritage Registry` (layer 10, item 85e758ffcd7a4aaeb8dd476143b70fd7):
    "a formal list of heritage resources ... with qualities or
    characteristics that are recognized as significant", 43 parcels on
    2026-11-22 -- the community's Heritage Register;
  * `Heritage Designation` (layer 9, item d2fcf6e69fd94401ae9ee1194465070c):
    the properties "protected by bylaw", 20 parcels, most of them also on the
    register.

The City's `Heritage Inventory` (layer 8, 172 rows) is a survey "with minimal
research", not a designation, and is not read -- Hamilton and Kingston took
the statutory register the same way.

Both layers publish the designated *parcel* as a polygon.  **A place is its
name**: the register repeats a place as several parcels (`BYRNES PROPERTIES`
is four, a house and its trees) and carries older and newer records of one
place side by side (`WHARF OFFICE (R/99-549)` and `Wharf Office`, `BANK OF
MONTREAL` twice), so rows are grouped by their name -- the designation-bylaw
suffix (`(R/99-549)`) removed, case folded -- and each group's parcels are
published as one MULTIPOLYGON.  `FeatureNumber` is no help: it is reused
across places and missing on some rows.  The id is that name
(`mpr-wharf-office`).

A designation parcel whose centre falls inside a register parcel is the same
place under a different label (`MASONIC TEMPLE (MOVED 1980)` is the register's
`MASONIC HALL`) and is skipped; the rest (`Carter Residence`, ...) are added.
A row marked `Demolished = Yes` is dropped.

`address` is `House Street`; `year_built` the first four-digit year in
`DateBuilt` (`Circa 1920`); `property_type` the layer's `Type` (House,
Business, Church, School, Cemetery, Trees).
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import emit

BASE = "https://geoservices.mapleridge.ca/server/rest/services/DataCatalog/Recreation/MapServer"
REGISTER = FeatureLayer(f"{BASE}/10", timeout=120)
DESIGNATION = FeatureLayer(f"{BASE}/9", timeout=120)
OUT_FIELDS = "OBJECTID,Name,House,Street,Type,DateBuilt,Demolished"

BYLAW_SUFFIX = re.compile(r"\s*\((?:R\s*/?\s*\d{2}-\d+|R\d{2}-\d+)\)\s*$", re.IGNORECASE)
TYPE_FIXES = {"Cemetary": "Cemetery"}
# Words kept upper-case when an all-caps register name is title-cased.
UPPER_WORDS = {"cpr", "ceed"}
EMPTY_STREET = {"civic n/a", "n/a"}


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("’", "'").split())
    return text or None


def display_name(raw: str) -> str:
    name = BYLAW_SUFFIX.sub("", raw).strip()
    if name.isupper():
        words = []
        for word in name.lower().split():
            if word.strip("()") in UPPER_WORDS:
                word = word.upper()
            else:
                word = "-".join(p[:1].upper() + p[1:] for p in word.split("-"))
                word = "/".join(p[:1].upper() + p[1:] for p in word.split("/"))
                if word.startswith("("):
                    word = "(" + word[1:2].upper() + word[2:]
            words.append(word)
        name = " ".join(words)
        name = re.sub(r"\bOf\b", "of", name)
        name = re.sub(r"\bThe\b(?!^)", "the", name)
        name = re.sub(r"\bAnd\b", "and", name)
        name = name[:1].upper() + name[1:]
    else:
        name = re.sub(r"\bOf\b", "of", name)
    return name


def key_of(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def address_of(rec: dict) -> str | None:
    house = clean(rec.get("House"))
    street = clean(rec.get("Street"))
    if street and street.lower() in EMPTY_STREET:
        street = None
    if not street:
        return None
    return f"{house} {street}" if house else street


def year_of(value) -> int | None:
    match = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", clean(value) or "")
    return int(match.group(1)) if match else None


def ring_centre(rings: list) -> tuple[float, float] | None:
    points = [p for ring in rings for p in ring if p and len(p) >= 2]
    if not points:
        return None
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


def inside(point: tuple[float, float], ring: list) -> bool:
    x, y = point
    hit = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def fetch(layer: FeatureLayer) -> list[dict]:
    rows: list[dict] = []
    for page in iter_features(
        layer, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326, order_by="OBJECTID"
    ):
        rows.extend(page)
    return rows


def group(features: list[dict], places: dict[str, dict]) -> None:
    for feature in features:
        rec = feature.get("attributes") or {}
        raw = clean(rec.get("Name"))
        rings = (feature.get("geometry") or {}).get("rings")
        if raw is None or not rings:
            continue
        if (clean(rec.get("Demolished")) or "").lower() == "yes":
            continue
        name = display_name(raw)
        place = places.setdefault(
            key_of(name),
            {"name": name, "rings": [], "address": None, "year": None, "type": None},
        )
        place["rings"].extend(rings)
        place["address"] = place["address"] or address_of(rec)
        place["year"] = place["year"] or year_of(rec.get("DateBuilt"))
        kind = clean(rec.get("Type"))
        place["type"] = place["type"] or (TYPE_FIXES.get(kind, kind) if kind else None)


def build() -> pa.Table:
    places: dict[str, dict] = {}
    group(fetch(REGISTER), places)
    register_rings = [ring for place in places.values() for ring in place["rings"]]

    extra: list[dict] = []
    for feature in fetch(DESIGNATION):
        rings = (feature.get("geometry") or {}).get("rings")
        centre = ring_centre(rings) if rings else None
        if centre is None or any(inside(centre, ring) for ring in register_rings):
            continue
        extra.append(feature)
    group(extra, places)

    landmark_id: list[str] = []
    name: list[str] = []
    geometry_raw: list[str] = []
    address: list[str | None] = []
    year_built: list[int | None] = []
    property_type: list[str | None] = []
    for key, place in sorted(places.items()):
        wkt = esri_geometry_to_wkt({"rings": place["rings"]})
        if wkt is None:
            continue
        landmark_id.append(f"mpr-{key}")
        name.append(place["name"])
        geometry_raw.append(wkt)
        address.append(place["address"])
        year_built.append(place["year"])
        property_type.append(place["type"])

    n = len(landmark_id)
    print(f"Maple Ridge landmarks: {n} place(s), {len(extra)} designation-only parcel(s)", file=sys.stderr)
    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(["CAMPR"] * n, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "year_built": pa.array(year_built, type=pa.int64()),
            "property_type": pa.array(property_type, type=pa.string()),
        }
    )


if __name__ == "__main__":
    emit(build())

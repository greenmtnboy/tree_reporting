#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""York Region's heritage properties, from the Region's own layer (no licence
stated; displayed with attribution only, not licensed for reuse).

Source: "York Region Heritage Properties", the Regional Municipality of York's
own hosted layer on its ArcGIS Online organisation (owner `YorkMunicipalGovt`),
read through its two views -- "Heritage Properties Northern Communities"
(item f3b4fb6fc1e34ec78225a48cbba869fe, 157 points) and "Heritage Properties
Southern Urban Centres" (item db502b5ba005478db0871aa4e1a2951b, 179) -- which
split one layer between them (OBJECTIDs never repeat across the two).  336
points on 2026-11-23, in all nine local municipalities (Markham 115,
Richmond Hill 35, Whitchurch-Stouffville 33, Newmarket 32, Vaughan 29, King
28, East Gwillimbury 25, Aurora 20, Georgina 19).  The item describes it as
"a centralized inventory of heritage properties ... compiled from municipal
heritage inventories", first made for the Region's Canada 150 heritage
application and behind its "Built to Last" story map: designated and notable
houses, barns, churches and pioneer cemeteries, each with a write-up.  It is
a curated selection, not a statutory register.

Why this and not a municipal register: Vaughan's Municipal Heritage Register
(972 points) and Richmond Hill's Inventory of Cultural Heritage Resources
(332) are each one municipality and state no licence either; Markham's
Heritage Property Locator layer refuses anonymous queries; the Region's open
data hub lists only Markham's Heritage Conservation District boundaries.  This
layer is the one source that covers the Region, from the trees' own publisher.

Licence: the items' licence field reads only "General Access. Available to
the public." (the Region's access class, not a licence) and they are not on
the open data hub, so the York Region Open Data Licence that covers the tree
layer does not name them -- displayed with attribution only.

**`Description` is HTML whose first line is `Address: ...`**; that is the
`address`.  A relocated building reads `Address: 1577 Major Mackenzie Drive
East, moved to 9920 Leslie Street` (or `changed to`), and the address after
`moved to` / `changed to` is where it stands now, which is the one kept.  A
leading `*` on the name (55 rows, the relocated ones) is a story-map marker
and is stripped; a U+FFFD in place of an apostrophe (`Cobbler�s
Cottage`) is restored.

**The id is the municipality and the layer's `Number`**
(`yrk-hp-{municipality}-{number}`): `Number` is each municipality's sequence
in the application and is unique within it (checked over both views); the
OBJECTID would re-key on a reload.  A repeat raises.
"""

import html
import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import emit, make_point_wkt

_SERVICES = "https://services1.arcgis.com/GzvOwaQBbX7KLiuG/arcgis/rest/services/"
LAYERS = (
    FeatureLayer(
        f"{_SERVICES}Heritage_Properties_Northern_Communities_/FeatureServer/0",
        timeout=120,
    ),
    FeatureLayer(
        f"{_SERVICES}Heritagen_Properties_Southern_Urban_Centres/FeatureServer/0",
        timeout=120,
    ),
)
OUT_FIELDS = "OBJECTID,Name,Description,Municipality,Number"

_TAG = re.compile(r"<[^>]+>")
_ADDRESS = re.compile(r"^\s*Address:\s*(.+)$", re.IGNORECASE)
_MOVED = re.compile(r",?\s*(?:moved|changed)\s+to\s+", re.IGNORECASE)


def clean(value) -> str | None:
    if value is None:
        return None
    text = html.unescape(str(value)).replace("�", "'")
    text = " ".join(text.split())
    return text or None


def first_line(description) -> str | None:
    if not description:
        return None
    text = html.unescape(str(description)).replace("�", "'")
    # Paragraph and line breaks end the first line; other tags are dropped.
    text = re.sub(r"</p>|<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = _TAG.sub("", text)
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            return line
    return None


def address_of(description) -> str | None:
    line = first_line(description)
    if not line:
        return None
    match = _ADDRESS.match(line)
    if not match:
        return None
    address = _MOVED.split(match.group(1))[-1].strip().rstrip(".")
    return address or None


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def transform(features: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    name: list[str] = []
    geometry_raw: list[str] = []
    address: list[str | None] = []
    seen: set[str] = set()

    for feature in features:
        rec = feature.get("attributes") or {}
        label = clean(rec.get("Name"))
        label = label.lstrip("*").strip() if label else None
        municipality = clean(rec.get("Municipality"))
        number = rec.get("Number")
        if not label or not municipality or number is None:
            continue
        lat, lon = esri_point(feature.get("geometry"))
        if lat is None or lon is None:
            continue
        key = f"yrk-hp-{slug(municipality)}-{int(number)}"
        if key in seen:
            raise RuntimeError(f"York Region landmarks: repeated id {key}")
        seen.add(key)
        landmark_id.append(key)
        name.append(label)
        geometry_raw.append(make_point_wkt(lon, lat))
        address.append(address_of(rec.get("Description")))

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(["CAYRK"] * len(landmark_id), type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
        }
    )


if __name__ == "__main__":
    features: list[dict] = []
    for layer in LAYERS:
        for page in iter_features(
            layer, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
        ):
            features.extend(page)
    table = transform(features)
    print(
        f"York Region landmarks: {table.num_rows} of {len(features)} features",
        file=sys.stderr,
    )
    emit(table)

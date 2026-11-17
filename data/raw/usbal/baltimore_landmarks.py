#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Baltimore City Landmarks and National Register listings in Baltimore City.

Two registers, both read live from ArcGIS layers:

* **The Baltimore City Landmark List**, the city's own designation register
  (designated by ordinance on the recommendation of the Commission for
  Historical and Architectural Preservation, CHAP), published on Open
  Baltimore as "Designated Landmarks" (ArcGIS item
  8a570598d14c41ada202a6a0155bb0aa, owner `baltimore_city`;
  `DesignatedLandmarks/FeatureServer/0`): 231 points on 2026-11-16.  Open
  Baltimore datasets are published "on an open license basis, with no
  restrictions on copying, publishing, further distributing, modifying, or
  using the data for any non-commercial or commercial purpose" (Baltimore
  City Code, Art. 1 § 9-8(b)).
  The layer is one row per point, not per landmark, so rows are grouped:
  `id` is the landmark-list number and several points share one (Patterson
  Park's three, St. Peter the Apostle's four), published as one landmark with
  a MULTIPOINT.  Two numbers hold differently named structures -- `23` is
  fifteen Druid Hill Park pavilions and buildings -- and those are one
  landmark per name.  A `(Public Interior)` designation of a building already
  listed under its number is folded into the building.  18 newer landmarks
  carry `id` = 0 and are grouped by name.  Keys: `balt-lm-{id}` for a number
  with one name, `balt-lm-{id}-{name slug}` for a shared number, and
  `balt-lm-{name slug}` for an unnumbered landmark.  A name marked razed
  (`Ascot House (Razed 1981)`) is not published.  The layer has no
  designation date (`edit_date` is the 2009 digitising), so `year_designated`
  is null.  CHAP's historic districts are areas and are not read.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) inside
  the city line.  NPS records Baltimore City under `County = Baltimore` with
  `City` spelt four ways, and some county listings carry a Baltimore mailing
  city, so neither column decides: a listing is kept when its point (a
  polygon's mean vertex) falls inside the city line, read from Open
  Baltimore's "Baltimore City Line" (item 8007e8f6bf09468ba2e5e68d172b91b1, a
  closed polyline).  Listed buildings, sites and structures are keyed
  `balt-nr-{NRIS_Refnum}` with `CertDate` as `year_designated`; districts are
  areas and are not published (Durham's rule).  NPS data is a US federal work.

A building on both registers keeps both rows (Columbus's rule): the two
designations are separate facts, and `lm_type` names the register.
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import CITY_BOUNDS, emit, make_point_wkt

LANDMARKS = FeatureLayer(
    "https://services1.arcgis.com/UWYHeuuJISiGmgXx/arcgis/rest/services/"
    "DesignatedLandmarks/FeatureServer/0"
)
LANDMARK_FIELDS = "OBJECTID,id,name,address"

CITY_LINE = FeatureLayer(
    "https://services1.arcgis.com/UWYHeuuJISiGmgXx/arcgis/rest/services/"
    "Baltimore_City_Line_New/FeatureServer/0"
)

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,State,CertDate,STATUS"
NRHP_WHERE = "State = 'MARYLAND'"

LOCAL_TYPE = "Baltimore City Landmark"
NATIONAL_TYPE = "National Register of Historic Places"

INTERIOR = re.compile(r"\s*\(public interior\)\s*$", re.IGNORECASE)
RAZED = re.compile(r"\b(razed|demolished)\b", re.IGNORECASE)

COLUMNS = (
    "landmark_id",
    "name",
    "geometry_raw",
    "address",
    "lm_type",
    "register_number",
    "year_designated",
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().replace("’", "'").replace("'", "")).strip("-")


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def city_landmarks() -> dict[str, list]:
    features = fetch_all(
        LANDMARKS, out_fields=LANDMARK_FIELDS, return_geometry=True, out_sr=4326
    )
    if not features:
        raise RuntimeError(f"{LANDMARKS.base} returned no landmarks")
    points: list[tuple[int, int, str, str | None, float, float]] = []
    skipped = 0
    for feature in sorted(features, key=lambda f: f["attributes"]["OBJECTID"]):
        attrs = feature.get("attributes") or {}
        geom = feature.get("geometry") or {}
        name = clean(attrs.get("name"))
        if not name or geom.get("x") is None or geom.get("y") is None:
            continue
        if RAZED.search(name):
            skipped += 1
            continue
        number = int(attrs.get("id") or 0)
        points.append((number, attrs["OBJECTID"], name, clean(attrs.get("address")),
                       float(geom["x"]), float(geom["y"])))

    # One landmark per (number, name), a "(Public Interior)" row folded into
    # the building of the same name; the name shown is the building row's.
    groups: dict[tuple[int, str], list] = defaultdict(list)
    names: dict[tuple[int, str], str] = {}
    for number, oid, name, address, x, y in points:
        base = INTERIOR.sub("", name)
        group = (number, slug(base))
        if group not in names or (base == name and INTERIOR.search(names[group])):
            names[group] = name
        groups[group].append((oid, address, x, y))
    groups = {(number, names[(number, s)]): members for (number, s), members in groups.items()}

    shared_numbers = {
        number for number in {k[0] for k in groups} if number
        and sum(1 for k in groups if k[0] == number) > 1
    }
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    seen: set[str] = set()
    for (number, name), members in sorted(groups.items(), key=lambda kv: min(m[0] for m in kv[1])):
        if not number:
            key = f"balt-lm-{slug(name)}"
        elif number in shared_numbers:
            key = f"balt-lm-{number}-{slug(name)}"
        else:
            key = f"balt-lm-{number}"
        if key in seen:
            raise RuntimeError(f"Baltimore landmark key {key} is repeated")
        seen.add(key)
        coords = sorted({(x, y) for _, _, x, y in members})
        if len(coords) == 1:
            wkt = make_point_wkt(*coords[0])
        else:
            wkt = "MULTIPOINT(" + ", ".join(f"({x} {y})" for x, y in coords) + ")"
        addresses = sorted({a for _, a, _, _ in members if a})
        rows["landmark_id"].append(key)
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append("; ".join(addresses) or None)
        rows["lm_type"].append(LOCAL_TYPE)
        rows["register_number"].append(str(number) if number else None)
        rows["year_designated"].append(None)
    print(
        f"Baltimore landmarks: {len(seen)} City Landmarks from {len(points)} points "
        f"({skipped} razed skipped)",
        file=sys.stderr,
    )
    return rows


def city_line() -> list[list[float]]:
    """The city line as one closed ring of (lon, lat)."""
    features = fetch_all(
        CITY_LINE, out_fields="FID", order_by="FID", return_geometry=True, out_sr=4326
    )
    paths = [p for f in features for p in ((f.get("geometry") or {}).get("paths") or [])]
    if len(paths) != 1 or len(paths[0]) < 4:
        raise RuntimeError(f"{CITY_LINE.base} is no longer one closed line ({len(paths)} paths)")
    return paths[0]


def inside(x: float, y: float, ring: list) -> bool:
    hit = False
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            hit = not hit
    return hit


def representative_point(geometry: dict | None) -> tuple[float, float] | None:
    if not geometry:
        return None
    if "x" in geometry:
        x, y = geometry.get("x"), geometry.get("y")
        return (float(x), float(y)) if x is not None and y is not None else None
    rings = geometry.get("rings") or []
    if not rings or not rings[0]:
        return None
    ring = rings[0]
    return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)


def national_register() -> dict[str, list]:
    ring = city_line()
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USBAL"]
    box = {
        "geometry": f"{lon_min},{lat_min},{lon_max},{lat_max}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }
    seen: set[str] = set()
    skipped = {"district": 0, "not listed": 0, "outside the city line": 0}
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    for layer in NRHP_LAYERS:
        for feature in fetch_all(
            layer, out_fields=NRHP_FIELDS, where=NRHP_WHERE, return_geometry=True,
            out_sr=4326, extra_params=box,
        ):
            attrs = feature.get("attributes") or {}
            ref = clean(attrs.get("NRIS_Refnum"))
            name = clean(attrs.get("RESNAME"))
            geometry = feature.get("geometry")
            wkt = esri_geometry_to_wkt(geometry)
            point = representative_point(geometry)
            if not ref or not name or wkt is None or point is None or ref in seen:
                continue
            if not inside(*point, ring):
                skipped["outside the city line"] += 1
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                skipped["not listed"] += 1
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                skipped["district"] += 1
                continue
            seen.add(ref)
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"balt-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["register_number"].append(ref)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    if not seen:
        raise RuntimeError("no National Register listings read for Baltimore")
    print(
        f"Baltimore landmarks: {len(seen)} National Register listings; skipped "
        + ", ".join(f"{n} {why}" for why, n in skipped.items()),
        file=sys.stderr,
    )
    return rows


def build() -> pa.Table:
    local = city_landmarks()
    national = national_register()
    data = {k: local[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USBAL"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

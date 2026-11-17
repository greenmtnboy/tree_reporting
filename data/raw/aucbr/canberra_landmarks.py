#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Canberra's heritage places: the ACT Heritage Register and the Commonwealth Heritage List.

Canberra is split between two governments, and so are its registers: the ACT
Heritage Register covers Territory land, and the places on National Land --
Parliament House Vista, the Australian War Memorial, Lake Burley Griffin, the
High Court -- are on the Commonwealth's list instead.  Both are read live.

* **ACT Heritage Register** ("ACTGOV Heritage Register", ArcGIS Online item
  d03d3fa41dcc49c4a4ea9058f3f44060, owner `ACTmapi`,
  `ACTGOV_Heritage_Register/FeatureServer/1`; "Creative Commons By
  Attribution 4.0 (Australian Capital Territory)" with the actmapi data
  terms).  7,495 block polygons on 2026-11-16, one row per block a place
  touches.  Published: registered (`HRcode` 3) and provisionally registered
  (2) places.  Not published: nominations (1) and unregistered Aboriginal
  places (4), and every row the register marks `DataRestrictions = 'Yes'` --
  restricted Aboriginal places the ACT has de-identified by joining them to
  whole blocks, so the polygon is not the place and the publisher has asked
  that it not be read as one.  A place's blocks are merged into one
  (multi)polygon by `NAME`: `HeritageID` cannot key it, being 0 or null on
  dozens of unrelated places and different on the blocks of one place
  (Booroomba Station has three).  Keyed `cbr-hr-{slug of the name}`, with the
  place's positive HeritageIDs as `register_number`.
* **Commonwealth Heritage List** (DCCEEW, item
  bf02ea66970c40d18b15e782b20a2fa9,
  `gis.environment.gov.au/.../Commonwealth_Heritage_List/FeatureServer/0`;
  "Attribution 3.0 Australia (CC BY 3.0 AU)").  `STATE = 'ACT'` rows with
  `STATUS = 'Listed place'` inside `CITY_BOUNDS["AUCBR"]` -- the register
  files Jervis Bay Territory under ACT, 150 km away on the coast, and the box
  drops it.  Keyed `cbr-chl-{PLACE_ID}`, with the listing year as
  `year_designated`.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_geometry_to_wkt,
    esri_ms_to_date,
    iter_features,
)
from shared.ingest import CITY_BOUNDS, emit

ACT_REGISTER = FeatureLayer(
    "https://services1.arcgis.com/E5n4f1VY84i0xSjy/arcgis/rest/services/"
    "ACTGOV_Heritage_Register/FeatureServer/1",
    timeout=180,
)
ACT_FIELDS = "OBJECTID,NAME,HeritageID,HRcategory,HRcode,DataRestrictions"
ACT_WHERE = "HRcode IN ('2', '3') AND (DataRestrictions IS NULL OR DataRestrictions <> 'Yes')"
ACT_TYPES = {
    "3": "ACT Heritage Register",
    "2": "ACT Heritage Register (provisional)",
}

CHL = FeatureLayer(
    "https://gis.environment.gov.au/gispubmap/rest/services/ogc_services/"
    "Commonwealth_Heritage_List/FeatureServer/0",
    timeout=180,
)
CHL_FIELDS = "OBJECTID,PLACE_ID,NAME,CLASS,STATUS,ADDRESS,REGISTER"
CHL_WHERE = "STATE = 'ACT' AND STATUS = 'Listed place'"
CHL_TYPE = "Commonwealth Heritage List"

COLUMNS = (
    "landmark_id", "name", "geometry_raw", "address", "category",
    "lm_type", "register_number", "year_designated",
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def category_of(value: str | None) -> str | None:
    """`Historic place or object` -> `Historic`; CHL's `CLASS` is already short."""
    text = clean(value)
    return text.split()[0] if text else None


def bounding_box() -> dict[str, str]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["AUCBR"]
    return {
        "geometry": f"{lon_min},{lat_min},{lon_max},{lat_max}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }


def act_register(rows: dict[str, list]) -> None:
    places: dict[str, dict] = {}
    for page in iter_features(
        ACT_REGISTER, out_fields=ACT_FIELDS, where=ACT_WHERE,
        return_geometry=True, out_sr=4326,
    ):
        for feature in page:
            attrs = feature.get("attributes") or {}
            name = clean(attrs.get("NAME"))
            rings = (feature.get("geometry") or {}).get("rings") or []
            if name is None or not rings:
                continue
            place = places.setdefault(
                name, {"rings": [], "ids": set(), "codes": set(), "categories": set()}
            )
            place["rings"].extend(rings)
            if attrs.get("HeritageID"):
                place["ids"].add(int(attrs["HeritageID"]))
            place["codes"].add(str(attrs.get("HRcode")))
            if category_of(attrs.get("HRcategory")):
                place["categories"].add(category_of(attrs.get("HRcategory")))
    if not places:
        raise RuntimeError(f"no registered places read from {ACT_REGISTER.base}")
    seen: set[str] = set()
    for name, place in sorted(places.items()):
        key = slug(name)
        wkt = esri_geometry_to_wkt({"rings": place["rings"]})
        if not key or wkt is None:
            continue
        if key in seen:
            raise RuntimeError(f"two ACT Heritage Register names slug to {key!r}")
        seen.add(key)
        # A place registered on any of its blocks is registered.
        code = "3" if "3" in place["codes"] else "2"
        categories = sorted(place["categories"])
        rows["landmark_id"].append(f"cbr-hr-{key}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(None)
        rows["category"].append(categories[0] if len(categories) == 1 else None)
        rows["lm_type"].append(ACT_TYPES[code])
        rows["register_number"].append(
            ", ".join(str(i) for i in sorted(place["ids"])) or None
        )
        rows["year_designated"].append(None)
    print(f"Canberra landmarks: {len(seen)} ACT Heritage Register places", file=sys.stderr)


def commonwealth_list(rows: dict[str, list]) -> None:
    seen: set[int] = set()
    for page in iter_features(
        CHL, out_fields=CHL_FIELDS, where=CHL_WHERE, return_geometry=True,
        out_sr=4326, extra_params=bounding_box(),
    ):
        for feature in page:
            attrs = feature.get("attributes") or {}
            place_id = attrs.get("PLACE_ID")
            name = clean(attrs.get("NAME"))
            wkt = esri_geometry_to_wkt(feature.get("geometry"))
            if place_id is None or name is None or wkt is None or place_id in seen:
                continue
            seen.add(place_id)
            listed = esri_ms_to_date(attrs.get("REGISTER"))
            rows["landmark_id"].append(f"cbr-chl-{int(place_id)}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("ADDRESS")))
            rows["category"].append(category_of(attrs.get("CLASS")))
            rows["lm_type"].append(CHL_TYPE)
            rows["register_number"].append(str(int(place_id)))
            rows["year_designated"].append(listed.year if listed else None)
    if not seen:
        raise RuntimeError(f"no Commonwealth Heritage List places read in Canberra from {CHL.base}")
    print(f"Canberra landmarks: {len(seen)} Commonwealth Heritage List places", file=sys.stderr)


def build() -> pa.Table:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    act_register(rows)
    commonwealth_list(rows)
    n = len(rows["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(rows["landmark_id"], type=pa.string()),
            "city": pa.array(["AUCBR"] * n, type=pa.string()),
            "name": pa.array(rows["name"], type=pa.string()),
            "geometry_raw": pa.array(rows["geometry_raw"], type=pa.string()),
            "address": pa.array(rows["address"], type=pa.string()),
            "category": pa.array(rows["category"], type=pa.string()),
            "lm_type": pa.array(rows["lm_type"], type=pa.string()),
            "register_number": pa.array(rows["register_number"], type=pa.string()),
            "year_designated": pa.array(rows["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

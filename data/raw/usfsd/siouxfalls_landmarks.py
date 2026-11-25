#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Sioux Falls, SD's local historic register and National Register listings.

Two registers, both read live from ArcGIS layers:

* **The Sioux Falls local register**, read from the City's "Historic
  Properties" layer (`Data/Community/MapServer/13` on gis.siouxfalls.gov,
  ArcGIS item 7b0407feca3e4f47bfe54559b9c1dd5d, "authoritative historic
  property polygons for Sioux Falls"; licence "The City of Sioux Falls
  provides open data licensed under the Creative Commons Attribution 4.0
  International License", the item's `licenseInfo`).  72 polygons on
  2026-11-22, each flagged `LOCAL_REGISTER` and `NATIONAL_REGISTER`; only the
  rows on the local register are read from it (16), with `LOCAL_REG_DATE` as
  `year_designated`.  The National Register rows of the same layer come from
  the NPS instead, which carries their reference numbers.  The layer has no
  identifier column (`Id` is 1 on every row), so the key is `OBJECTID`
  (`fsd-lr-{OBJECTID}`): the layer is an edited geodatabase table whose ids
  run with gaps (1..2035), not a reload.  A repeated OBJECTID raises.
  The City's "Historic Districts" (layer 12) are areas and are not read.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) within
  `CITY_BOUNDS["USFSD"]` and with `City = Sioux Falls` -- the box also holds
  the Bridge at Iverson Crossing, filed under Brandon.  Listed buildings,
  sites and structures, keyed `fsd-nr-{NRIS_Refnum}` with `CertDate` as
  `year_designated`; districts are areas and are not published (Durham's
  rule).  NPS data is a US federal work.

Nine properties are on both registers; both rows are kept, as Columbus keeps
them, because the two designations are separate facts.  `lm_type` says which
register each row is from.
"""

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
from shared.ingest import emit
from shared.nrhp import national_register as shared_national_register  # noqa: E402

REGISTER = FeatureLayer(
    "https://gis.siouxfalls.gov/arcgis/rest/services/Data/Community/MapServer/13"
)
REGISTER_FIELDS = "OBJECTID,DESCRIP,LOCATION,LOCAL_REGISTER,LOCAL_REG_DATE"


LOCAL_TYPE = "Sioux Falls local historic register"

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


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def local_register() -> dict[str, list]:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    features = fetch_all(
        REGISTER, out_fields=REGISTER_FIELDS, return_geometry=True, out_sr=4326
    )
    if not features:
        raise RuntimeError(f"{REGISTER.base} returned no properties")
    seen: set[int] = set()
    for feature in features:
        attrs = feature.get("attributes") or {}
        oid = attrs.get("OBJECTID")
        if oid in seen:
            raise RuntimeError(f"Sioux Falls Historic Properties OBJECTID {oid} is repeated")
        seen.add(oid)
        if clean(attrs.get("LOCAL_REGISTER")) != "Yes":
            continue
        name = clean(attrs.get("DESCRIP"))
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if not name or wkt is None:
            continue
        listed = esri_ms_to_date(attrs.get("LOCAL_REG_DATE"))
        rows["landmark_id"].append(f"fsd-lr-{oid}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(clean(attrs.get("LOCATION")))
        rows["lm_type"].append(LOCAL_TYPE)
        rows["register_number"].append(None)
        rows["year_designated"].append(listed.year if listed else None)
    return rows


def national_register() -> dict[str, list]:
    return shared_national_register("USFSD", "Sioux Falls", "fsd", COLUMNS, required=False)


def build() -> pa.Table:
    local = local_register()
    national = national_register()
    data = {k: local[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USFSD"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

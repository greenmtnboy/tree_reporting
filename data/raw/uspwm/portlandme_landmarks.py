#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Portland, Maine's local historic landmarks and National Register listings.

Two registers, both read live from ArcGIS layers:

* **The City's Local Landmarks**, `HistoricResources/Local_Landmarks/MapServer/0`
  on the City's own server gis.portlandmaine.gov (portal item
  13d2773085ab41a1afad4f79d7b4b9d6, owner `zpowell@PORTLANDMAINE`,
  "Locally designated historic landmarks in Portland, Maine as mapped in
  July 2025"): the landmark table joined onto the designated parcels, so
  each row is a parcel polygon with the landmark's name, street address,
  designating Council order and designation date.  126 rows on
  2026-11-27; two landmarks (J.B. Brown Memorial Block, Marine Hospital)
  appear twice on the same parcel and are published once.  The layer is a
  join output, so its OBJECTID and GlobalIDs are not stable; the key is the
  landmark's lead parcel (`landmarks_leadcbl`, the assessor's
  chart-block-lot, `003  L001001`): `portlandme-lm-003-L001001`.  Two
  different landmarks on one lead parcel raise.  `landmarks_designation_date`
  is `year_designated`.  No licence is stated (the item's `licenseInfo` is
  empty); displayed with attribution only.
* **The National Register of Historic Places** (`shared.nrhp`), within
  `CITY_BOUNDS["USPWM"]` and with `City = Portland` -- the box also holds
  South Portland, Westbrook and Falmouth, whose listings name those towns.
  Listed buildings, sites, structures and objects keyed
  `portlandme-nr-{NRIS_Refnum}`; districts are areas and are not published.
  NPS data is a US federal work.

A property on both registers keeps both rows, as Davenport and Columbus do:
the two designations are separate facts.  `lm_type` says which register
each row is from.  The City's older `Historic_Landmarks` layer (90 rows,
last edited 2022) is superseded by the 2025 one and not read; its historic
districts and landscapes are areas and are not published.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features  # noqa: E402
from shared.ingest import emit  # noqa: E402
from shared.nrhp import COLUMNS, national_register  # noqa: E402

LOCAL = FeatureLayer(
    "https://gis.portlandmaine.gov/maps/rest/services/HistoricResources/"
    "Local_Landmarks/MapServer/0"
)
LOCAL_FIELDS = (
    "OBJECTID,landmarks_leadcbl,landmarks_landmark_name,landmarks_street_number,"
    "landmarks_street_name,landmarks_designation_date"
)
PREFIX = "portlandme"
LOCAL_TYPE = "Portland, Maine local historic landmark"

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def designation_year(value) -> int | None:
    if value in (None, ""):
        return None
    return (_EPOCH + timedelta(milliseconds=float(value))).year


def local_register() -> dict[str, list]:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    features = [
        feature
        for page in iter_features(
            LOCAL, out_fields=LOCAL_FIELDS, return_geometry=True, out_sr=4326,
            order_by="OBJECTID",
        )
        for feature in page
    ]
    if not features:
        raise RuntimeError(f"{LOCAL.base} returned no landmarks")
    seen: dict[str, str] = {}
    repeats = skipped = 0
    for feature in features:
        attrs = feature.get("attributes") or {}
        cbl = clean(attrs.get("landmarks_leadcbl"))
        name = clean(attrs.get("landmarks_landmark_name"))
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if not cbl or not name or wkt is None:
            skipped += 1
            continue
        key = cbl.replace(" ", "-")
        if key in seen:
            if seen[key] != name:
                raise RuntimeError(
                    f"Portland, ME landmarks: lead parcel {cbl} holds both "
                    f"{seen[key]!r} and {name!r}; the key no longer names one landmark"
                )
            repeats += 1
            continue
        seen[key] = name
        address = " ".join(
            part for part in (
                clean(attrs.get("landmarks_street_number")),
                (clean(attrs.get("landmarks_street_name")) or "").title() or None,
            ) if part
        )
        rows["landmark_id"].append(f"{PREFIX}-lm-{key}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(address or None)
        rows["lm_type"].append(LOCAL_TYPE)
        rows["register_number"].append(None)
        rows["year_designated"].append(designation_year(attrs.get("landmarks_designation_date")))
    print(
        f"Portland, ME local landmarks: {len(features)} rows, {len(seen)} published, "
        f"{repeats} repeated parcels, {skipped} without a parcel, name or shape",
        file=sys.stderr,
    )
    return rows


def build() -> pa.Table:
    local = local_register()
    national = national_register("USPWM", "Portland", PREFIX)
    data = {k: local[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USPWM"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

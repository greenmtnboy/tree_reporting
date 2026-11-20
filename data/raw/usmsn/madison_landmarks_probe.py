#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Madison, WI's landmarks.

The latest of three stamps, so the weekly landmark lane only rebuilds
Madison's parquet when one of the registers has changed:

* the Local Landmarks layer: a MapServer layer with no `editingInfo` and no
  edit column, so the newest designation date `LLM_Date` (2009-05-19 when the
  city was wired; the four newest rows have none) -- it moves when a landmark
  is designated with its date filled in;
* as a backstop for an edit that does not, that layer's ArcGIS Online item's
  `modified`;
* the NPS National Register rows inside `CITY_BOUNDS["USMSN"]`: the newest
  `EDIT_DATE` over them (the whole box, not only the Madison rows, because a
  listing whose `City` is corrected into Madison must move it too).
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_ms_to_datetime,
    field_max,
    iter_features,
)
from shared.ingest import CITY_BOUNDS, emit_freshness, get_json_with_retry

REGISTER = FeatureLayer(
    "https://maps.cityofmadison.com/arcgis/rest/services/Public/OPEN_DATA_PLANNING/MapServer/7"
)
REGISTER_ITEM = "279a5ad5f2f94d0bb20f898c523d5b5a"
ITEM_URL = f"https://www.arcgis.com/sharing/rest/content/items/{REGISTER_ITEM}"

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))


def register_item_modified() -> datetime:
    payload = get_json_with_retry(ITEM_URL, params={"f": "json"}, timeout=60)
    stamp = esri_ms_to_datetime(payload.get("modified"))
    if stamp is None:
        raise RuntimeError(f"no `modified` on ArcGIS item {REGISTER_ITEM}")
    return stamp


def national_register_last_edit() -> datetime:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USMSN"]
    box = {
        "geometry": f"{lon_min},{lat_min},{lon_max},{lat_max}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }
    stamps = [
        esri_ms_to_datetime((feature.get("attributes") or {}).get("EDIT_DATE"))
        for layer in NRHP_LAYERS
        for page in iter_features(layer, out_fields="OBJECTID,EDIT_DATE", extra_params=box)
        for feature in page
    ]
    stamps = [s for s in stamps if s is not None]
    if not stamps:
        raise RuntimeError("no National Register EDIT_DATE inside Madison's bounds")
    return max(stamps)


def fetch_modified_at() -> datetime:
    return max(
        field_max(REGISTER, "LLM_Date"),
        register_item_modified(),
        national_register_last_edit(),
    )


if __name__ == "__main__":
    emit_freshness("USMSN", fetch_modified_at, label="USMSN landmarks")

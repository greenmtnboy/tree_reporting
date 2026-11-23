#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Grand Junction, CO's landmarks.

The later of two stamps, so the weekly landmark lane only rebuilds Grand
Junction's parquet when a register has changed:

* the City's Historic Sites layer: a MapServer layer with no `editingInfo`
  and no date column, so its ArcGIS Online item's `modified`;
* the NPS National Register rows inside `CITY_BOUNDS["USGJT"]`: the newest
  `EDIT_DATE` over them (the whole box, not only the Grand Junction rows, so
  a listing whose `City` is corrected into Grand Junction moves it too).
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_datetime, iter_features
from shared.ingest import CITY_BOUNDS, emit_freshness, get_json_with_retry

SITES_ITEM = "474f2237f2c44e6bbe5b6588d7638a43"
ITEM_URL = f"https://www.arcgis.com/sharing/rest/content/items/{SITES_ITEM}"

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))


def sites_item_modified() -> datetime:
    payload = get_json_with_retry(ITEM_URL, params={"f": "json"}, timeout=60)
    stamp = esri_ms_to_datetime(payload.get("modified"))
    if stamp is None:
        raise RuntimeError(f"no `modified` on ArcGIS item {SITES_ITEM}")
    return stamp


def national_register_last_edit() -> datetime:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USGJT"]
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
        raise RuntimeError("no National Register EDIT_DATE inside Grand Junction's bounds")
    return max(stamps)


def fetch_modified_at() -> datetime:
    return max(sites_item_modified(), national_register_last_edit())


if __name__ == "__main__":
    emit_freshness("USGJT", fetch_modified_at, label="USGJT landmarks")

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Davenport, Iowa's landmarks.

The latest of two stamps, so the weekly landmark lane only rebuilds
Davenport's parquet when a register has changed:

* the City's `Individually_Listed_LHL` layer: no `editingInfo` and no
  edit-date column, so its portal item's `modified` on the City's own
  ArcGIS Enterprise portal (gis.davenportiowa.com/portal);
* the NPS National Register rows inside `CITY_BOUNDS["USDVN"]`: the newest
  `EDIT_DATE` over them (the whole box, not only Davenport's rows, so a
  listing whose `City` is corrected into Davenport moves it too).
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_datetime, iter_features  # noqa: E402
from shared.ingest import CITY_BOUNDS, emit_freshness, get_json_with_retry  # noqa: E402

REGISTER_ITEM = "ee0cc2a528b94b9e9508371957690d58"
ITEM_URL = f"https://gis.davenportiowa.com/portal/sharing/rest/content/items/{REGISTER_ITEM}"

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))


def register_item_modified() -> datetime:
    payload = get_json_with_retry(ITEM_URL, params={"f": "json"}, timeout=60)
    stamp = esri_ms_to_datetime(payload.get("modified"))
    if stamp is None:
        raise RuntimeError(f"no `modified` on Davenport portal item {REGISTER_ITEM}")
    return stamp


def national_register_last_edit() -> datetime:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USDVN"]
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
        raise RuntimeError("no National Register EDIT_DATE inside Davenport's bounds")
    return max(stamps)


def fetch_modified_at() -> datetime:
    return max(register_item_modified(), national_register_last_edit())


if __name__ == "__main__":
    emit_freshness("USDVN", fetch_modified_at, label="USDVN landmarks")

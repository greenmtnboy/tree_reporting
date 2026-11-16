#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Naperville, IL's landmarks.

The later of the two registers' stamps, so the weekly landmark lane only
rebuilds Naperville's parquet when one of them has changed:

* the heritage-plaque layer: a hosted layer whose `editingInfo` carries no
  date (`lastEditDate: null`, editor tracking off) and which is not listed
  on the city's Hub, so what is left is its ArcGIS Online item's own
  `modified` (2017-04-26 when the city was wired) -- a catalogue stamp, which
  moves when the layer is overwritten or its item edited;
* the NPS National Register rows inside `CITY_BOUNDS["USNPV"]`: the newest
  `EDIT_DATE` over them (2024-05-29, Aurora's Big Woods School; the probe
  watches the whole box, not only the Naperville rows, because a listing
  whose `City` is corrected into Naperville must move it too).
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_datetime, iter_features
from shared.ingest import CITY_BOUNDS, emit_freshness, get_json_with_retry

PLAQUE_ITEM = "ae8ca80aee364223aa71dd09eecdfbc3"
ITEM_URL = f"https://www.arcgis.com/sharing/rest/content/items/{PLAQUE_ITEM}"

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))


def plaque_item_modified() -> datetime:
    payload = get_json_with_retry(ITEM_URL, params={"f": "json"}, timeout=60)
    stamp = esri_ms_to_datetime(payload.get("modified"))
    if stamp is None:
        raise RuntimeError(f"no `modified` on ArcGIS item {PLAQUE_ITEM}")
    return stamp


def national_register_last_edit() -> datetime:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USNPV"]
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
        raise RuntimeError("no National Register EDIT_DATE inside Naperville's bounds")
    return max(stamps)


def fetch_modified_at() -> datetime:
    return max(plaque_item_modified(), national_register_last_edit())


if __name__ == "__main__":
    emit_freshness("USNPV", fetch_modified_at, label="USNPV landmarks")

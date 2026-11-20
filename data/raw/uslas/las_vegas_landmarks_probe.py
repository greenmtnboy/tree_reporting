#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Las Vegas, NV's landmarks.

The later of the two registers' stamps, so the weekly landmark lane only
rebuilds Las Vegas's parquet when one of them has changed:

* the City's Historic_Areas layer's `editingInfo.dataLastEditDate`
  (2026-11-19 when the city was wired; the Department of Planning republishes
  the layer, so this moves more often than the register does -- harmless on a
  weekly lane);
* the NPS National Register rows inside `CITY_BOUNDS["USLAS"]`: the newest
  `EDIT_DATE` over them (the whole box, not only the rows inside the City's
  limits, so a listing moved into the city moves it too).

The City limits layer the landmark script reads is not probed: a boundary
change that adds a listing is rare and is picked up on the next register edit.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_ms_to_datetime,
    iter_features,
    layer_last_edit,
)
from shared.ingest import CITY_BOUNDS, emit_freshness

CITY_REGISTER = FeatureLayer(
    "https://services1.arcgis.com/F1v0ufATbBQScMtY/arcgis/rest/services/"
    "HistoricAreas/FeatureServer/163",
    timeout=60,
)

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))


def national_register_last_edit() -> datetime:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USLAS"]
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
        raise RuntimeError("no National Register EDIT_DATE inside Las Vegas's bounds")
    return max(stamps)


def fetch_modified_at() -> datetime:
    return max(layer_last_edit(CITY_REGISTER), national_register_last_edit())


if __name__ == "__main__":
    emit_freshness("USLAS", fetch_modified_at, label="USLAS landmarks")

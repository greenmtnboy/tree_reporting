#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Loveland, Colorado's landmarks.

The latest of two stamps, so the weekly landmark lane only rebuilds
Loveland's parquet when a register has changed:

* the City's hosted `Historic_Properties/FeatureServer/5` layer: its
  `editingInfo.dataLastEditDate`, which `layer_last_edit` reads (and raises
  if the block disappears);
* the NPS National Register rows inside `CITY_BOUNDS["USLVL"]`: the newest
  `EDIT_DATE` over them (the whole box, not only Loveland's rows, so a
  listing whose `City` is corrected into Loveland moves it too).
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (  # noqa: E402
    FeatureLayer,
    esri_ms_to_datetime,
    iter_features,
    layer_last_edit,
)
from shared.ingest import CITY_BOUNDS, emit_freshness  # noqa: E402

LOCAL = FeatureLayer(
    "https://services1.arcgis.com/d4MV7N9xmuHYbLrF/arcgis/rest/services/"
    "Historic_Properties/FeatureServer/5"
)

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))


def national_register_last_edit() -> datetime:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USLVL"]
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
        raise RuntimeError("no National Register EDIT_DATE inside Loveland's bounds")
    return max(stamps)


def fetch_modified_at() -> datetime:
    return max(layer_last_edit(LOCAL), national_register_last_edit())


if __name__ == "__main__":
    emit_freshness("USLVL", fetch_modified_at, label="USLVL landmarks")

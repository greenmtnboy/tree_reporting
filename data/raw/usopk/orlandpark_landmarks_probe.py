#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Orland Park, Illinois's landmarks.

The later of two stamps, so the weekly landmark lane only rebuilds Orland
Park's parquet when a register has changed:

* the Village's Old Orland contributing-structure layer: its own
  `editingInfo.dataLastEditDate` (2022-06-14 when the city was wired);
* the NPS National Register rows inside `CITY_BOUNDS["USOPK"]`: the newest
  `EDIT_DATE` over them (the whole box, not only the Orland Park rows, so a
  listing whose `City` is corrected into Orland Park moves it too).
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

DISTRICT = FeatureLayer(
    "https://services3.arcgis.com/sVDuYsm37AMvFL49/arcgis/rest/services/"
    "OOHContributingStructure_Point/FeatureServer/0",
    timeout=60,
)

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))


def national_register_last_edit() -> datetime:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USOPK"]
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
        raise RuntimeError("no National Register EDIT_DATE inside Orland Park's bounds")
    return max(stamps)


def fetch_modified_at() -> datetime:
    return max(layer_last_edit(DISTRICT), national_register_last_edit())


if __name__ == "__main__":
    emit_freshness("USOPK", fetch_modified_at, label="USOPK landmarks")

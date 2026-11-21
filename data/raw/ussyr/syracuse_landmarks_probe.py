#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Syracuse's landmarks.

The newest NPS National Register `EDIT_DATE` over the rows inside
`CITY_BOUNDS["USSYR"]` (the whole box, not only the rows inside the City
boundary, so a listing moved into the city moves it too).  The City
boundary itself is not stamped: a boundary change is rare enough to ride the
next Register edit.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (  # noqa: E402
    FeatureLayer,
    esri_ms_to_datetime,
    iter_features,
)
from shared.ingest import CITY_BOUNDS, emit_freshness  # noqa: E402

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0", timeout=60), FeatureLayer(f"{NRHP}/1", timeout=60))


def fetch_modified_at() -> datetime:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USSYR"]
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
        raise RuntimeError("no National Register EDIT_DATE inside Syracuse's bounds")
    return max(stamps)


if __name__ == "__main__":
    emit_freshness("USSYR", fetch_modified_at, label="USSYR landmarks")

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Rochester, NY's landmarks.

The newest `EDIT_DATE` over the NPS National Register rows inside (a
padded) `CITY_BOUNDS["USROC"]` -- the whole box, not only the rows inside the
City boundary, so a listing whose position is corrected into the city moves
it too.  The City boundary itself is not watched: it has not changed since
the city was wired and a change would not alter which listings it holds
without an NPS edit near it.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_datetime, iter_features
from shared.ingest import CITY_BOUNDS, emit_freshness

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))


def fetch_modified_at() -> datetime:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USROC"]
    pad = 0.01
    box = {
        "geometry": f"{lon_min - pad},{lat_min - pad},{lon_max + pad},{lat_max + pad}",
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
        raise RuntimeError("no National Register EDIT_DATE inside Rochester's bounds")
    return max(stamps)


if __name__ == "__main__":
    emit_freshness("USROC", fetch_modified_at, label="USROC landmarks")

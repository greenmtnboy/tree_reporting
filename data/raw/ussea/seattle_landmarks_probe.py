#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Seattle, WA's landmarks.

The later of the two registers' stamps, so the weekly landmark lane only
rebuilds Seattle's parquet when one of them has changed:

* the City Landmarks layer: the newest `EFF_DATE` (2026-08 when the city was
  wired), which moves when a landmark is designated -- the register's edits
  are designations.  Not the layer's `editingInfo` or the item's `modified`:
  the hosted layer is reloaded daily, so both move every day whether or not
  a landmark changed.  A designation entered without an `EFF_DATE` (155 rows
  have none) does not move it; the next dated designation picks it up.
* the NPS National Register rows inside `CITY_BOUNDS["USSEA"]`: the newest
  `EDIT_DATE` over them (the probe watches the whole box, not only the
  Seattle rows, because a listing whose `City` is corrected into Seattle
  must move it too).
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
from shared.ingest import CITY_BOUNDS, emit_freshness

REGISTER = FeatureLayer(
    "https://services.arcgis.com/ZOyb2t4B0UYuYNYH/arcgis/rest/services/Landmarks/FeatureServer/0"
)

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))


def national_register_last_edit() -> datetime:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USSEA"]
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
        raise RuntimeError("no National Register EDIT_DATE inside Seattle's bounds")
    return max(stamps)


def fetch_modified_at() -> datetime:
    return max(field_max(REGISTER, "EFF_DATE"), national_register_last_edit())


if __name__ == "__main__":
    emit_freshness("USSEA", fetch_modified_at, label="USSEA landmarks")

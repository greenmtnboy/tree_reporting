#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for San Diego, CA's landmarks.

The later of the two registers' stamps, so the weekly landmark lane only
rebuilds San Diego's parquet when one of them has changed:

* the City's HRB designated-resources layer: a MapServer layer with no
  `editingInfo`, so the newest of its own `last_edited_date` and
  `created_date` (2026-10 when the city was wired; most of the older rows
  carry neither, the rows added or moved since the layer went into editor
  tracking do);
* the NPS National Register rows inside `CITY_BOUNDS["USSAN"]`: the newest
  `EDIT_DATE` over them (the whole box, so a listing whose `City` is
  corrected into San Diego moves it too).

`field_max` raises when a field is gone, a schema question that must not
degrade to "no new data"; an outage is caught inside it and does degrade.
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

HRB = FeatureLayer(
    "https://webmaps.sandiego.gov/arcgis/rest/services/Planning/"
    "Historic_Preservation_Resources/MapServer/0",
    timeout=180,
)

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))


def national_register_last_edit() -> datetime:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USSAN"]
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
        raise RuntimeError("no National Register EDIT_DATE inside San Diego's bounds")
    return max(stamps)


def fetch_modified_at() -> datetime:
    return max(
        field_max(HRB, "last_edited_date"),
        field_max(HRB, "created_date"),
        national_register_last_edit(),
    )


if __name__ == "__main__":
    emit_freshness("USSAN", fetch_modified_at, label="USSAN landmarks")

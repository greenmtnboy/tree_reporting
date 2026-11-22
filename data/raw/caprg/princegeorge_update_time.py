#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Prince George's tree inventory.

The `Trees` layer (`OpenData_ParkData/FeatureServer/0`) is a hosted feature
layer and publishes `editingInfo`, so the watermark is its
`dataLastEditDate` (2026-11-14 when the city was wired).  The City's
`opendatascripts_cityofpg` account re-inserts the whole layer when it
republishes, so the stamp moves at each republish, which is exactly when the
inventory can have changed.

An outage degrades through `get_json_with_retry`; a layer that stops
publishing `editingInfo` raises, because that is a schema change.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services2.arcgis.com/CnkB6jCzAsyli34z/arcgis/rest/services/"
    "OpenData_ParkData/FeatureServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("CAPRG", fetch_modified_at)

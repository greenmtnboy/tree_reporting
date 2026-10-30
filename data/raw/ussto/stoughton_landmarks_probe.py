#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Stoughton, WI's historic landmark register.

The layer's own `editingInfo.dataLastEditDate`, so the weekly landmark lane
only rebuilds Stoughton's parquet when the Stoughton Landmarks Commission has
actually edited the register.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services7.arcgis.com/iZIPdzAfqdnP9vrA/arcgis/rest/services/"
    "Historical_Landmarks_WFL1/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USSTO", fetch_modified_at, label="USSTO landmarks")

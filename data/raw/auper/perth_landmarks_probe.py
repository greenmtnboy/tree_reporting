#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Perth's heritage list.

The layer's own `editingInfo.dataLastEditDate`, so the weekly landmark lane
only rebuilds Perth's parquet when the city has actually edited its heritage
list.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

# Kept in step with perth_landmarks.py's LAYER.
LAYER = FeatureLayer(
    "https://services7.arcgis.com/v8XBa2naYNQGOjlG/arcgis/rest/services/"
    "City_of_Perth_Heritage_List_CPS2/FeatureServer/1"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("AUPER", fetch_modified_at, label="AUPER landmarks")

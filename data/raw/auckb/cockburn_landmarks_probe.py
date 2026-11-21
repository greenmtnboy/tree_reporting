#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Cockburn's heritage inventory.

The layer's own `editingInfo.dataLastEditDate`, so the landmark lane only
rebuilds Cockburn's parquet when the City has edited its heritage layer.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

# Kept in step with cockburn_landmarks.py's LAYER.
LAYER = FeatureLayer(
    "https://services-ap1.arcgis.com/pvAgeoXPDsVDgOQ0/arcgis/rest/services/"
    "Heritage_Map_Public_WFL1/FeatureServer/2"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("AUCKB", fetch_modified_at, label="AUCKB landmarks")

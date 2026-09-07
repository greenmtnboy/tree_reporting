#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Moncton's cultural asset inventory.

The layer's own `editingInfo.dataLastEditDate`, so the weekly landmark lane
only rebuilds Moncton's parquet when the inventory has actually been edited.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, layer_last_edit
from _ingest_shared import emit_freshness

LAYER = FeatureLayer(
    "https://services1.arcgis.com/E26PuSoie2Y7bbyI/arcgis/rest/services/"
    "Cultural_Assets/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("CAMON", fetch_modified_at, label="CAMON landmarks")

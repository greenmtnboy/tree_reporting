#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Bassendean's heritage list.

The layer's own `editingInfo.dataLastEditDate`, so the weekly landmark lane
only rebuilds Bassendean's parquet when the Town has actually edited its
heritage list.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

# Kept in step with bassendean_landmarks.py's LAYER.
LAYER = FeatureLayer(
    "https://services-ap1.arcgis.com/551UnqKK1GZeDKxQ/arcgis/rest/services/"
    "Public_TOB_102_Council_Adopted_Heritage_26_Jul_2022_view/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("AUBSD", fetch_modified_at, label="AUBSD landmarks")

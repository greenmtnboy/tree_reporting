#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for MIT's campus tree layer (see mit_tree_info.py).

A hosted FeatureServer with `editingInfo`, so the watermark is the layer's
own `dataLastEditDate`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services5.arcgis.com/FXrk7QY7CN707pF1/arcgis/rest/services/"
    "NEW_Administrative_Basemap_Layers/FeatureServer/56"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USCAM", fetch_modified_at)

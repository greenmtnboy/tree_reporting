#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Stoughton, WI's tree inventory.

The hosted layer publishes `editingInfo`, so the watermark is one metadata
read.  Its `dataLastEditDate` read 2026-10-20 when the city was wired; the
city's forestry staff edit the layer in the field through the season.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services7.arcgis.com/iZIPdzAfqdnP9vrA/arcgis/rest/services/"
    "Tree_Inventory_(Public)/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USSTO", fetch_modified_at)

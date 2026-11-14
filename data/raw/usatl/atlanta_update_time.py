#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Trees Atlanta's plant inventory.

The public view publishes the hosted layer's `editingInfo`, so the watermark
is one metadata read.  Its `dataLastEditDate` read 2026-11-10 when the city
was wired: Trees Atlanta's crews edit the inventory from the field through
the planting season (November to March), so expect it to move most days
then and rarely in summer.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services6.arcgis.com/BZn8g8tzu5WfMokL/arcgis/rest/services/"
    "Trees_Atlanta_Plant_Inventory_Public_View/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USATL", fetch_modified_at)

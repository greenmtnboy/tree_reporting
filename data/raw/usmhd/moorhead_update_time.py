#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Moorhead's tree inventory.

The Treeplotter_Inventory Trees layer is a hosted FeatureServer with
`editingInfo`, so the watermark is its `lastEditDate` (Esri epoch
milliseconds, UTC).  It has stood at 2024-08-02 since the City's one bulk
load; a republish moves it.  An outage degrades through `emit_freshness`; a
layer that stops publishing the stamp raises.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services.arcgis.com/t2tplTEKxuL7eSvF/arcgis/rest/services/"
    "Treeplotter_Inventory/FeatureServer/2"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USMHD", fetch_modified_at)

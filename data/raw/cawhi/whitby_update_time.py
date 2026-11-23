#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Whitby's tree inventory.

The layer is hosted on ArcGIS Online with editing tracked, so the watermark is
its `editingInfo.dataLastEditDate` (2025-12-18 when the city was wired, the
"Updated December 2025" the item states).  An outage degrades to the epoch; a
layer that stops publishing `editingInfo` raises.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services5.arcgis.com/ATdLnvuMRJk8AGkQ/arcgis/rest/services/"
    "Trees/FeatureServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("CAWHI", fetch_modified_at)

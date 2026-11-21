#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Medicine Hat's tree inventory.

The `Park_Tree_Inventory` layer is a hosted ArcGIS Online feature layer with
editing tracked, so the watermark is its own `editingInfo.dataLastEditDate`
(2026-11-21 when the city was wired: the City's forestry staff edit it live).
The stamp is epoch milliseconds, a real UTC instant.

An outage degrades through `get_json_with_retry`; a layer that stops
publishing `editingInfo` raises, because that is a schema change.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services3.arcgis.com/2gQ2Dv4Fb016qFVU/arcgis/rest/services/"
    "Park_Tree_Inventory/FeatureServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("CAMHT", fetch_modified_at)

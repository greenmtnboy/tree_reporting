#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Medicine Hat's historic resources map.

`Historic_Map_Tour_layer` is a hosted ArcGIS Online layer with editing
tracked, so the watermark is its `editingInfo.dataLastEditDate` (2026-02-20
when the city was wired), a UTC instant.  An outage degrades; a layer that
stops publishing `editingInfo` raises.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services3.arcgis.com/2gQ2Dv4Fb016qFVU/arcgis/rest/services/"
    "Historic_Map_Tour_layer/FeatureServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("CAMHT", fetch_modified_at, label="CAMHT landmarks")

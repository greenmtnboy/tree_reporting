#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Port St. Lucie's tree inventory.

`PSL_Trees/FeatureServer/2` is a hosted ArcGIS Online layer with editing
tracked, so the watermark is its `editingInfo.dataLastEditDate`
(2026-01-28 when the city was wired; the City reloads the layer a few times a
year).  An outage degrades through `emit_freshness`; a layer that stops
publishing `editingInfo` raises.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness
from shared.platforms.arcgis import FeatureLayer, layer_last_edit

LAYER = FeatureLayer(
    "https://services1.arcgis.com/YdUP5V6WwzeG8T8r/arcgis/rest/services/"
    "PSL_Trees/FeatureServer/2",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USPSL", fetch_modified_at)

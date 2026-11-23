#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Port St. Lucie's landmarks.

The City's `PARKS` layer is a hosted ArcGIS Online feature layer with editing
tracked, so the watermark is its `editingInfo.dataLastEditDate` (2025-09-05
when the city was wired).  An outage degrades; a layer that stops publishing
`editingInfo` raises.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness
from shared.platforms.arcgis import FeatureLayer, layer_last_edit

PARKS = FeatureLayer(
    "https://services1.arcgis.com/YdUP5V6WwzeG8T8r/arcgis/rest/services/PARKS/FeatureServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(PARKS)


if __name__ == "__main__":
    emit_freshness("USPSL", fetch_modified_at, label="USPSL landmarks")

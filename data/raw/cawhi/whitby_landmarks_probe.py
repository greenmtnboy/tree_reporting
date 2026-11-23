#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Whitby's landmarks.

Both layers (`WhitbyParks`, `WhitbyTownFacilities`) are hosted ArcGIS Online
feature layers with editing tracked, so the watermark is the later of their
`editingInfo.dataLastEditDate` stamps.  An outage degrades; a layer that stops
publishing `editingInfo` raises.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

BASE = "https://services5.arcgis.com/ATdLnvuMRJk8AGkQ/arcgis/rest/services"
LAYERS = (
    FeatureLayer(f"{BASE}/WhitbyParks/FeatureServer/0", timeout=60),
    FeatureLayer(f"{BASE}/WhitbyTownFacilities/FeatureServer/0", timeout=60),
)


def fetch_modified_at() -> datetime:
    return max(layer_last_edit(layer) for layer in LAYERS)


if __name__ == "__main__":
    emit_freshness("CAWHI", fetch_modified_at, label="CAWHI landmarks")

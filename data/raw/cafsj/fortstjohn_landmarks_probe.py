#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Fort St. John's landmarks.

Both layers (`Recreation Areas`, `Points of Interest`) are hosted ArcGIS
Online feature layers with editing tracked, so the watermark is the later of
their `editingInfo.dataLastEditDate` stamps (2025-12-22 and 2026-04-09 when
the city was wired).  An outage degrades; a layer that stops publishing
`editingInfo` raises.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

BASE = "https://services.arcgis.com/uFSCv839a7S1MRb7/arcgis/rest/services"
LAYERS = (
    FeatureLayer(f"{BASE}/Recreation_Areas/FeatureServer/0", timeout=60),
    FeatureLayer(f"{BASE}/Points_of_Interest/FeatureServer/0", timeout=60),
)


def fetch_modified_at() -> datetime:
    return max(layer_last_edit(layer) for layer in LAYERS)


if __name__ == "__main__":
    emit_freshness("CAFSJ", fetch_modified_at, label="CAFSJ landmarks")

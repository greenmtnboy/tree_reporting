#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Guelph's heritage register.

Both layers of `CulturalHeritageResources` (hosted on ArcGIS Online) publish
`editingInfo`; the later `dataLastEditDate` of the two is the watermark
(2024-05-22 when the city was wired).  `layer_last_edit` degrades on an outage
and raises when `editingInfo` disappears, which is a schema change.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

SERVICE = (
    "https://services5.arcgis.com/k3dd78JyG9GFoZHG/arcgis/rest/services/"
    "CulturalHeritageResources/FeatureServer"
)
LAYERS = (
    FeatureLayer(f"{SERVICE}/0", timeout=60),
    FeatureLayer(f"{SERVICE}/1", timeout=60),
)


def fetch_modified_at() -> datetime:
    return max(layer_last_edit(layer) for layer in LAYERS)


if __name__ == "__main__":
    emit_freshness("CAGLP", fetch_modified_at, label="CAGLP landmarks")

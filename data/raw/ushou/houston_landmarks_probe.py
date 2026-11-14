#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Houston's historic landmark register.

The hosted view's own `editingInfo.dataLastEditDate` (2025-06-04 when the city
was wired), so the weekly landmark lane only rebuilds Houston's parquet when
the Planning & Development Department has edited the register.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services.arcgis.com/NummVBqZSIJKUeVR/arcgis/rest/services/"
    "COH_Historic_Sites_and_Landmarks_view/FeatureServer/6"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USHOU", fetch_modified_at, label="USHOU landmarks")

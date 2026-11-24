#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Redlands's tree inventory.

The hosted `Street_Trees/FeatureServer/0` layer publishes `editingInfo`, so
the watermark is its `lastEditDate` (September 2023 on 2026-11-23: the
inventory is edited rarely, and a rebuild is a single 54k-row read).  An
outage degrades through `emit_freshness`; a layer that stops publishing the
stamp raises, because that is a schema change.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services.arcgis.com/FLM8UAw9y5MmuVTV/arcgis/rest/services/Street_Trees/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USRDL", fetch_modified_at)

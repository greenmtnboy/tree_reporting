#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Kitchener's heritage register.

The "Heritage_Properties" layer publishes `editingInfo`; its
`dataLastEditDate` moves when the City republishes the register (the item
says daily).  A layer that stops publishing it raises.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services1.arcgis.com/qAo1OsXi67t7XgmS/arcgis/rest/services/"
    "Heritage_Properties/FeatureServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("CAKIT", fetch_modified_at, label="CAKIT landmarks")

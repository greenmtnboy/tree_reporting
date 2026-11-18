#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Kitchener's tree inventory.

The "Tree_Inventory" layer publishes `editingInfo`; its `dataLastEditDate`
(2026-10-30 when the city was wired) moves when the City republishes the
layer, which the item says happens daily.  `layer_last_edit` reads it; an
outage degrades through `get_json_with_retry`, and a layer that stops
publishing `editingInfo` raises, because that is a schema change.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services1.arcgis.com/qAo1OsXi67t7XgmS/arcgis/rest/services/"
    "Tree_Inventory/FeatureServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("CAKIT", fetch_modified_at)

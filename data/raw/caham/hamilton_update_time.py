#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Hamilton's tree inventory.

The "Public Tree Inventory" view publishes `editingInfo`, and its
`dataLastEditDate` moves with the forestry team's edits to the underlying
layer (2026-11-17 when the city was wired -- the inventory is edited most
days).  `layer_last_edit` reads it; an outage degrades through
`get_json_with_retry`, and a layer that stops publishing `editingInfo`
raises, because that is a schema change.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services.arcgis.com/rYz782eMbySr2srL/arcgis/rest/services/"
    "Public_Tree_Inventory/FeatureServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("CAHAM", fetch_modified_at)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Bend, Oregon's tree inventory.

The Volunteer Tree Inventory layer is the live Survey123 layer, edited in
place as the survey goes on, so its own `editingInfo.dataLastEditDate` moves
when a row changes (`lastEditDate` also moves on a schema-only edit and is
the fallback only).  The item's `modified` stamp does not follow row edits.

An outage degrades through `get_json_with_retry`; a layer that stops
publishing `editingInfo` raises (`layer_last_edit`).
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services5.arcgis.com/JisFYcK2mIVg9ueP/arcgis/rest/services/"
    "service_b8c085f8c6f543658e06d452bf211869/FeatureServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USBND", fetch_modified_at)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Las Vegas, NV's tree inventory.

The ingest reads `Trees_PRD/FeatureServer/10` ("Park Trees"), a hosted layer
with editor tracking; its `editingInfo.dataLastEditDate` moves on every insert,
update, delete and republish (2026-11-19 03:18 UTC when the city was wired; the
item description's "Last updated: Nov 18 2026" suggests a scheduled republish
from the City's Cityworks database, so this may move daily).

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
    "https://services1.arcgis.com/F1v0ufATbBQScMtY/arcgis/rest/services/"
    "Trees_PRD/FeatureServer/10",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USLAS", fetch_modified_at)

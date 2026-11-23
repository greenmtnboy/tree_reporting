#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Salem, Oregon's tree inventory.

The layer (`CityTrees/FeatureServer/0`) is edited in place -- its OBJECTIDs
have the gaps deleted trees leave, not the dense block of a reload -- so its
own `editingInfo.dataLastEditDate` moves only when a row changes (2026-07-12
when the city was wired; `lastEditDate` is later, a schema-only edit, and is
not read).  The `EditDate` column is null on every row and cannot be used.

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
    "https://services.arcgis.com/kIA6yS9KDGqZL7U3/arcgis/rest/services/"
    "CityTrees/FeatureServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USSLM", fetch_modified_at)

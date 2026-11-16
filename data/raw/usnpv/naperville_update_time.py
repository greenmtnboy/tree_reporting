#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Naperville, IL's right-of-way tree inventory.

Reads the hosted layer's own `editingInfo.dataLastEditDate` (2026-05-07 when
the city was wired; the per-row `last_edited_date` agrees, its maximum being
2026-05-06) -- one metadata response, so a refresh only re-downloads the
95k-row layer when Forestry has edited it.

`layer_last_edit` raises when the layer stops publishing `editingInfo`, a
schema question that must not degrade to "no new data"; an outage is caught
by `get_json_with_retry` inside it and does degrade.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services1.arcgis.com/rXJ6QApc2sOtl1Pd/arcgis/rest/services/"
    "ROW_Tree_Inventory/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USNPV", fetch_modified_at)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Springfield, MO's tree inventory.

Reads the hosted layer's own `editingInfo.dataLastEditDate` (2026-07-31 when
the city was wired) -- one metadata response, so a refresh only re-downloads
the 26k-row layer when the city has edited or reloaded it.

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
    "https://services1.arcgis.com/aOss8CrQf3pARS5q/arcgis/rest/services/"
    "Tree_Inventory/FeatureServer/34"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USSPF", fetch_modified_at)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Richmond, VA's tree inventory.

The ingest reads `Tree_Inventory_Public_View/FeatureServer/0`, a hosted view
of Urban Forestry's live editing layer; the view publishes the source's
`editingInfo`, whose `dataLastEditDate` moves on every insert, update and
delete (2026-11-18 when the city was wired -- the layer is edited daily).

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
    "https://services1.arcgis.com/k3vhq11XkBNeeOfM/arcgis/rest/services/"
    "Tree_Inventory_Public_View/FeatureServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USRIC", fetch_modified_at)

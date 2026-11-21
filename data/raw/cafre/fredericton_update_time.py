#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Fredericton's tree inventory.

The City's `CRM_View_Tree_Inventory_Full` view publishes `editingInfo`, and
its `dataLastEditDate` moves with the forestry team's edits to the underlying
hosted layer (2026-03-30 when the city was wired; the catalogue view
`Tree_Inventory` carries the same stamps).  `layer_last_edit` reads it; an
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
    "https://services2.arcgis.com/iLWAxhpxafhOza2U/arcgis/rest/services/"
    "CRM_View_Tree_Inventory_Full/FeatureServer/37",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("CAFRE", fetch_modified_at)

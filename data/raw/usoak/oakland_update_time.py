#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Oakland's tree inventory.

The ingest reads `Oakland_Public_Tree_Inventory_/FeatureServer/0` in the City
of Oakland's ArcGIS Online organisation, a hosted layer that publishes
`editingInfo`; the probe is its `dataLastEditDate` (2023-08-08 when the city
was wired: the layer is a published survey snapshot, not an edited
work-management table).  Park & Tree Services has so far published each new
version as a new item ("Oakland_Tree_Inventory_0423" before this one), which
this probe cannot see: a replacement layer is a re-wire, not a refresh.

An outage degrades through `get_json_with_retry`; metadata without
`editingInfo` raises (`layer_last_edit`).
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services.arcgis.com/9tC74aDHuml0x5Yz/arcgis/rest/services/"
    "Oakland_Public_Tree_Inventory_/FeatureServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USOAK", fetch_modified_at)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Bassendean's tree inventory.

Reads the hosted view's own `editingInfo.dataLastEditDate` -- one small
metadata response -- so a refresh only re-downloads the inventory when the
Town has actually edited it.  The item's `modified` stamp is not used: it
moves when the item page is edited, not when the data does.

`layer_last_edit` raises rather than degrading when the layer publishes no
`editingInfo`: that is our mapping being wrong, not the portal being down.  A
genuine outage is caught by `get_json_with_retry` inside it and does degrade.

The layer's name carries a version ("..._Corrected_July_2026_v1"): when the
Town publishes a successor view this one stops moving.  Its GlobalIDs would
re-key the city, so a switch is a deliberate edit, never automatic.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

# Kept in step with bassendean_tree_info.py's LAYER.
LAYER = FeatureLayer(
    "https://services-ap1.arcgis.com/551UnqKK1GZeDKxQ/arcgis/rest/services/"
    "Public_View_TOB_213_All_Trees_Corrected_July_2026_v1/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("AUBSD", fetch_modified_at)

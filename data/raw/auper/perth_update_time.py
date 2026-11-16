#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Perth's tree inventory.

Reads the hosted view's own `editingInfo.dataLastEditDate` -- one small
metadata response against the 17k-point layer -- so a refresh only
re-downloads the inventory when the parks team has actually edited it.  The
item's `modified` stamp is not used: it moves when the item page is edited
(2024-10-10 when this was written) and not when the data does (2026-10-31).

`layer_last_edit` raises rather than degrading when the layer publishes no
`editingInfo`: that is our mapping being wrong, not the portal being down.  A
genuine outage is caught by `get_json_with_retry` inside it and does degrade.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

# Kept in step with perth_tree_info.py's LAYER.
LAYER = FeatureLayer(
    "https://services7.arcgis.com/v8XBa2naYNQGOjlG/arcgis/rest/services/"
    "PKS_AST_TREESMASTER_PV/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("AUPER", fetch_modified_at)

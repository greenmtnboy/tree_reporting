#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Mississauga's tree inventory.

The layer publishes `editingInfo`, so this is the cheapest of the three
watermarks: one metadata read, no statistics query and no Hub feed.  Its
`dataLastEditDate` read 2026-06-30 when the city was wired, which matches the
Hub catalogue's own `modified` stamp for the dataset to the minute.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, layer_last_edit
from _ingest_shared import emit_freshness

LAYER = FeatureLayer(
    "https://services6.arcgis.com/hM5ymMLbxIyWTjn2/arcgis/rest/services/"
    "2023_City_Owned_Tree_Inventory/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("CAMIS", fetch_modified_at)

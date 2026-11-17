#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Sacramento, CA's city-maintained trees.

The hosted layer publishes `editingInfo`, so this reads
`dataLastEditDate` from the layer resource (2026-11-15 when the city was
wired).  The layer is refreshed from the city's asset system by a scheduled
sync -- `dataLastEditDate`, `schemaLastEditDate` and the item's `modified`
all carry the same instant -- so the stamp can move on a sync that changed
nothing; that costs a rebuild, never a missed edit.

`layer_last_edit` raises when `editingInfo` is gone, a schema question that
must not degrade to "no new data"; an outage is caught by
`get_json_with_retry` inside it and does degrade.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services5.arcgis.com/54falWtcpty3V47Z/arcgis/rest/services/"
    "City_Maintained_Trees/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USSAC", fetch_modified_at)

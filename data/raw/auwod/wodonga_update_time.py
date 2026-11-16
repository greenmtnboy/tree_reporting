#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Wodonga's street and reserve trees.

The layer's own `editingInfo.dataLastEditDate` (2026-11-15T10:07Z on
2026-11-16), one small metadata read against the 94,652-row layer.  The item
says the layer is "reprocessed ... daily", so the stamp may move on days the
asset register did not; that costs a rebuild at the city's twice-weekly tick
and nothing more.

`layer_last_edit` raises when the layer publishes no `editingInfo` (a schema
question, which must not freeze the city silently) and degrades through
`get_json_with_retry` on an outage.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services-ap1.arcgis.com/w6r4LlwgJu8O0neQ/arcgis/rest/services/"
    "Street_and_reserve_trees/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("AUWOD", fetch_modified_at)

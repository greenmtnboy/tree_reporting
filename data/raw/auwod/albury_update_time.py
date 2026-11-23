#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Albury City Council's "Trees (Public)" layer.

The layer's own `editingInfo.dataLastEditDate` (2026-11-22T22:59Z on
2026-11-23), one small metadata read.  The view sits over the council's
working asset layer, so the stamp moves with day-to-day asset edits; that
costs a rebuild at Wodonga's tick and nothing more.

`layer_last_edit` raises when the layer publishes no `editingInfo` (a schema
question, which must not freeze the partition silently) and degrades through
`get_json_with_retry` on an outage.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services1.arcgis.com/UytyQvWmxkbjUtjg/arcgis/rest/services/"
    "Trees_(Public)/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("AUWOD", fetch_modified_at, label="AUWOD Albury")

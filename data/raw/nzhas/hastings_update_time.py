#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Hastings's tree inventory.

`Hastings_DC_Trees/FeatureServer/0` is a hosted feature layer with
`editingInfo`; its `dataLastEditDate` moves when the council republishes the
trees (2025-09-17 when the city was wired, matching the item's "Last Updated
September 2025").  Esri stamps are UTC milliseconds, so no zone handling is
needed.  A layer that stops publishing `editingInfo` raises (a schema
question); an outage degrades through `emit_freshness`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services1.arcgis.com/8L3DQUzjrkgEmDpQ/arcgis/rest/services/"
    "Hastings_DC_Trees/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("NZHAS", fetch_modified_at)

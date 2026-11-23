#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Lansing's tree inventory.

The `Trees/FeatureServer/1` layer is a hosted feature layer with
`editingInfo`, so the watermark is its `dataLastEditDate` (the rows' last
change; 2025-12-15 on 2026-11-22: the City republishes it a few times a
year).  A missing `editingInfo` raises (a schema question); an outage
degrades through `emit_freshness`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services1.arcgis.com/pNPbgWy7hpfFGWoZ/arcgis/rest/services/Trees/FeatureServer/1"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USLAN", fetch_modified_at)

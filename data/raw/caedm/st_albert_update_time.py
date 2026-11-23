#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for the City of St. Albert's Tree Inventory.

The layer is a hosted ArcGIS Online feature layer with editing tracked, so the
watermark is its own `editingInfo.dataLastEditDate` (epoch milliseconds, a
real UTC instant).  The city reloads it weekly by ETL, so the stamp moves
weekly whether or not a tree changed; that costs one re-download a week.

An outage degrades through `get_json_with_retry`; a layer that stops
publishing `editingInfo` raises, because that is a schema change.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services1.arcgis.com/fyyY0cNXvmUWvX1x/arcgis/rest/services/"
    "Tree_Inventory/FeatureServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("CAEDM", fetch_modified_at)

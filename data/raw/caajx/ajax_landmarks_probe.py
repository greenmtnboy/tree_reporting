#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Ajax's heritage inventory.

The layer publishes no `editingInfo` but carries `MODIFIED_DATE`, so the
weekly landmark lane only rebuilds Ajax's parquet when the inventory has
actually been edited.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, field_max
from _ingest_shared import emit_freshness

LAYER = FeatureLayer(
    "https://ajaxmaps.ajax.ca/gisernie/rest/services/Public/"
    "Ajax_Open_Data/MapServer/2"
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "MODIFIED_DATE")


if __name__ == "__main__":
    emit_freshness("CAAJX", fetch_modified_at, label="CAAJX landmarks")

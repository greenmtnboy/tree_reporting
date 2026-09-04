#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Denver's historic landmark register.

The layer's own `editingInfo.dataLastEditDate`, so the weekly landmark lane
only rebuilds Denver's parquet when the Landmark Preservation Commission has
actually edited the register.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, layer_last_edit
from _ingest_shared import emit_freshness

LAYER = FeatureLayer(
    "https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/"
    "ODC_HIST_LANDMARKSTRUCTURE_P/FeatureServer/69"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USDEN", fetch_modified_at, label="USDEN landmarks")

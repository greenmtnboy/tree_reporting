#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Halifax's heritage property register.

The layer's own `editingInfo.dataLastEditDate`, so the weekly landmark lane
only rebuilds Halifax's parquet when HRM has actually edited the register.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, layer_last_edit
from _ingest_shared import emit_freshness

LAYER = FeatureLayer(
    "https://services2.arcgis.com/11XBiaBYA9Ep0yNJ/arcgis/rest/services/"
    "Heritage_Properties/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("CAHFX", fetch_modified_at, label="CAHFX landmarks")

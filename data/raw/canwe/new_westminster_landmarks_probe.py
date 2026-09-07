#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for New Westminster's Heritage Register.

The layer's own `editingInfo.dataLastEditDate`, so the weekly landmark lane
only rebuilds New Westminster's parquet when the city has actually edited the
register.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, layer_last_edit
from _ingest_shared import emit_freshness

LAYER = FeatureLayer(
    "https://services3.arcgis.com/A7O8YnTNtzRPIn7T/arcgis/rest/services/"
    "Heritage_Register_view/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("CANWE", fetch_modified_at, label="CANWE landmarks")

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Mississauga's city landmark register.

The layer's own `editingInfo.dataLastEditDate`.  This one moves often -- the
POI register is edited most days -- so the weekly landmark lane will rebuild
Mississauga's parquet most weeks, which is correct: it is the register that
actually changes.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, layer_last_edit
from _ingest_shared import emit_freshness

LAYER = FeatureLayer(
    "https://services6.arcgis.com/hM5ymMLbxIyWTjn2/arcgis/rest/services/"
    "CITY_POI/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("CAMIS", fetch_modified_at, label="CAMIS landmarks")

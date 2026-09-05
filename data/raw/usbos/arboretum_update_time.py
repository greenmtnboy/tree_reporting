#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for the Arnold Arboretum's plant records.

A MapServer layer with no `editingInfo`, so the watermark is MAX(SDE_DT).
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, field_max
from _ingest_shared import emit_freshness

LAYER = FeatureLayer(
    "https://gis.arboretum.harvard.edu/arcgis/rest/services/Maps/Explorer/MapServer/34"
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "SDE_DT")


if __name__ == "__main__":
    emit_freshness("USBOS", fetch_modified_at)

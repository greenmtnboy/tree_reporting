#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for DC's Urban Forestry street trees.

A MapServer layer with no `editingInfo`, so the watermark is
MAX(LAST_EDITED_DATE).
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, field_max
from _ingest_shared import emit_freshness

LAYER = FeatureLayer(
    "https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/"
    "Urban_Tree_Canopy/MapServer/23"
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "LAST_EDITED_DATE")


if __name__ == "__main__":
    emit_freshness("USWAS", fetch_modified_at)

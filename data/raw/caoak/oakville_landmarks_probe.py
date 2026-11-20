#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Oakville's heritage register.

The "Heritage Properties Open Data" map-service layer publishes no
`editingInfo`, so this is MAX(`last_edited_date`), the geodatabase's
editor-tracking stamp (2026-10 when the city was wired).  `field_max` raises
when the field is gone, a schema change; an outage degrades.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://maps.oakville.ca/oakgis/rest/services/ArcGISOnline/"
    "AGO_OpenData/MapServer/26",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "last_edited_date")


if __name__ == "__main__":
    emit_freshness("CAOAK", fetch_modified_at, label="CAOAK landmarks")

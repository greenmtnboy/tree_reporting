#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Kelowna's heritage registry.

The layer publishes no `editingInfo` and carries no date column -- its schema
is `KID`, `BLDG_NAME` and the geometry -- so this falls back to the Hub
catalogue's `modified` stamp.  See `hub_last_modified` for what that costs;
the weekly landmark lane bounds it, and a heritage register moves on a scale
of years.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import hub_last_modified
from _ingest_shared import emit_freshness

HUB_HOST = "opendata.kelowna.ca"
LAYER_URL = (
    "https://geoportal.kelowna.ca/arcgis/rest/services/ArcGISOnline/"
    "OpenData_Planning_and_other/MapServer/9"
)


def fetch_modified_at() -> datetime:
    return hub_last_modified(HUB_HOST, LAYER_URL)


if __name__ == "__main__":
    emit_freshness("CAKEL", fetch_modified_at, label="CAKEL landmarks")

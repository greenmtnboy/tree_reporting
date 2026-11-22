#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Maple Ridge's heritage register.

Both layers (`Heritage Registry`, `Heritage Designation`) are MapServer layers
on the City's own ArcGIS Server with no `editingInfo` and no edit-date column
-- their schema is the name, address, type and build date -- so this falls
back to the Open Government portal's catalogue `modified` stamps, the later
of the two (2024-03-18 for both when the city was wired).  See
`hub_last_modified` for what that costs; the weekly landmark lane bounds it,
and a heritage register moves on a scale of years.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import hub_last_modified
from shared.ingest import emit_freshness

HUB_HOST = "opengov2-mapleridge.opendata.arcgis.com"
BASE = "https://geoservices.mapleridge.ca/server/rest/services/DataCatalog/Recreation/MapServer"
LAYER_URLS = (f"{BASE}/10", f"{BASE}/9")


def fetch_modified_at() -> datetime:
    return max(hub_last_modified(HUB_HOST, url) for url in LAYER_URLS)


if __name__ == "__main__":
    emit_freshness("CAMPR", fetch_modified_at, label="CAMPR landmarks")

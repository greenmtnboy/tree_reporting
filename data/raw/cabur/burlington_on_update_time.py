#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Burlington, Ontario's tree inventory.

The layer publishes no `editingInfo` and its schema carries no edit-date column
at all -- `YEAR_PLANTED` is the only date in it -- so neither `layer_last_edit`
nor `field_max` has anything to read, and this falls back to the Hub
catalogue's `modified` stamp.  See `hub_last_modified` for what that costs and
why it is the last of the three watermarks.

The stamp reads 2023-05-17, which is the oldest of any city on the map.  That
is a real property of this source rather than a broken probe: Burlington
publishes a periodic export and the service has not been republished since.
The city's twice-weekly cron still has work to do -- an approved community
submission and the weekly OSM extract both make this Parquet stale
independently of the portal -- and the day the export is refreshed the
catalogue stamp moves and the next tick picks it up.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import hub_last_modified
from _ingest_shared import emit_freshness

HUB_HOST = "navburl-burlington.opendata.arcgis.com"
LAYER_URL = (
    "https://mapping.burlington.ca/arcgisweb/rest/services/COB/"
    "Urban_Forestry/MapServer/0"
)


def fetch_modified_at() -> datetime:
    return hub_last_modified(HUB_HOST, LAYER_URL)


if __name__ == "__main__":
    emit_freshness("CABUR", fetch_modified_at)

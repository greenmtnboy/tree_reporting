#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Victoria's prominent heritage sites.

The layer publishes no `editingInfo` and carries no date column -- its
`created_date` is a string, not an Esri date, so `field_max` cannot read it --
so this falls back to the Hub catalogue's `modified` stamp.  See
`hub_last_modified` for what that costs; the weekly landmark lane is the floor
that bounds it, and a register of 23 OCP-designated buildings changes on a
scale of years.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import hub_last_modified
from _ingest_shared import emit_freshness

HUB_HOST = "opendata.victoria.ca"
LAYER_URL = (
    "https://maps.victoria.ca/server/rest/services/OpenData/"
    "OpenData_PlanningAndDevelopment/MapServer/8"
)


def fetch_modified_at() -> datetime:
    return hub_last_modified(HUB_HOST, LAYER_URL)


if __name__ == "__main__":
    emit_freshness("CAVIC", fetch_modified_at, label="CAVIC landmarks")

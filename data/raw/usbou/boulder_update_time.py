#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Boulder, CO's tree inventory.

The layer is a MapServer layer and publishes no `editingInfo`, so
`layer_last_edit` has nothing to read; every row carries its own
`LAST_EDITED_DATE` instead, and `field_max` asks the server for its maximum
as one statistics row (2026-11-13 19:15 UTC when the city was wired -- the
Urban Forestry Division edits the inventory most weeks).

`field_max` raises when the statistic comes back empty, because that is the
column having been renamed or dropped, not the server being down; an outage
is caught by `get_json_with_retry` inside it and degrades to the epoch.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://gis.bouldercolorado.gov/ags_svr2/rest/services/parks/TreesOpenData/MapServer/0"
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "LAST_EDITED_DATE")


if __name__ == "__main__":
    emit_freshness("USBOU", fetch_modified_at)

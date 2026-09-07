#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Victoria's tree inventory.

The layer publishes no `editingInfo` and its schema carries no date column, so
neither `layer_last_edit` nor `field_max` has anything to read and this falls
back to the Hub catalogue's `modified` stamp.  See `hub_last_modified` for
what that costs and why it is the last of the three watermarks.

Victoria's stamp is also the oldest in this batch (2024-11), which is not a
sign the probe is broken -- the whole `OpenData_Parks` service was last
republished then.  If it starts to move, the twice-weekly cron picks the
change up on the next tick.
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
    "OpenData_Parks/MapServer/15"
)


def fetch_modified_at() -> datetime:
    return hub_last_modified(HUB_HOST, LAYER_URL)


if __name__ == "__main__":
    emit_freshness("CAVIC", fetch_modified_at)

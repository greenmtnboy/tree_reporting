#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Lethbridge's municipal tree inventory.

The layer publishes no `editingInfo` and its schema carries no date column at
all -- `planted` is a year, not a write time -- so neither `layer_last_edit`
nor `field_max` has anything to read, and this falls back to the third
watermark: the Hub catalogue's `modified` stamp for the dataset.

See `hub_last_modified` for what that costs; the short version is that it is
the catalogue's stamp rather than the data's, so a service overwritten in
place may not move it.  The twice-weekly cron is the floor that bounds it,
and community submissions and the weekly OSM extract make CALET's parquet
stale through their own probes regardless.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import hub_last_modified
from _ingest_shared import emit_freshness

HUB_HOST = "opendata.lethbridge.ca"
LAYER_URL = (
    "https://gis.lethbridge.ca/gisopendata/rest/services/OpenData/"
    "odl_trees/MapServer/0"
)


def fetch_modified_at() -> datetime:
    return hub_last_modified(HUB_HOST, LAYER_URL)


if __name__ == "__main__":
    emit_freshness("CALET", fetch_modified_at)

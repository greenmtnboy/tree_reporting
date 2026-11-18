#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests", "tzdata"]
# ///
"""Freshness probe for Wellington's tree inventory.

`Parks/Parks/MapServer/53` is a MapServer layer: no `editingInfo`, and no
edit-date column.  Two stamps, and the probe takes the later:

* the council hub's DCAT `modified` for the dataset served at that layer
  (`hub_last_modified`; 2026-10-01 when the city was wired), which moves when
  the service is republished;
* MAX(`last_visited`), the survey date of the most recently inspected tree
  (2024-07-01 when wired).  The stamps are dates at midnight; like
  Christchurch's they are read as New Zealand wall time labelled UTC, so the
  watermark never sits in the future.

A missing dataset in the feed or an empty statistics row raises (a schema
question); an outage degrades through `emit_freshness`.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max, hub_last_modified
from shared.ingest import emit_freshness

LOCAL_TZ = ZoneInfo("Pacific/Auckland")

HUB_HOST = "data-wcc.opendata.arcgis.com"
LAYER_URL = "https://gis.wcc.govt.nz/arcgis/rest/services/Parks/Parks/MapServer/53"
LAYER = FeatureLayer(LAYER_URL)


def fetch_modified_at() -> datetime:
    catalogue = hub_last_modified(HUB_HOST, LAYER_URL)
    visited = field_max(LAYER, "last_visited")
    visited = visited.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc)
    return max(catalogue, visited)


if __name__ == "__main__":
    emit_freshness("NZWLG", fetch_modified_at)

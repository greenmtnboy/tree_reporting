#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests", "tzdata"]
# ///
"""Freshness probe for Christchurch's tree inventory.

The `OpenData/Tree` layer on gis.ccc.govt.nz publishes no `editingInfo`, so
the probe reads MAX(`LastEditDate`) over the whole layer with one statistics
query (2026-11-17 16:09 when the city was wired; the council edits the
inventory most working days).  The editor stamps look like New Zealand
wall-clock time labelled UTC (the maximum was half a day ahead of real UTC
when read), so the probe re-reads the stamp as Pacific/Auckland wall time
before converting it to UTC; otherwise the watermark would sit up to 13 hours
in the future.  `LastEditDate` is stamped on every edit, a removal
included -- a removed tree stays in the layer with `ServiceStatus = Removed`
-- so the maximum moves whenever the published set can.

The ArcGIS Online item's `modified` (2026-04-13) is not used: it is the
catalogue entry, not the rows.

`field_max` raises when the field is gone or the statistics row is empty, a
schema question that must not degrade to "no new data"; an outage is caught
by `get_json_with_retry` inside it and does degrade.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Pacific/Auckland")

LAYER = FeatureLayer(
    "https://gis.ccc.govt.nz/server/rest/services/OpenData/Tree/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    stamp = field_max(LAYER, "LastEditDate")
    return stamp.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("NZCHC", fetch_modified_at)

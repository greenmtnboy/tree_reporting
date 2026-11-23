#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Missoula's tree inventory.

The layer (`TreeLocations_mso/FeatureServer/0`) is a copy of the forestry
inventory that the City's Data Interoperability account truncates and
reloads whole, so its `editingInfo.dataLastEditDate` moves on every reload
whether or not a tree changed, and would rebuild the city on every tick.
What moves only with the inventory is `inventory_date`, which the forester
stamps on a site at every visit -- planting, removing or re-measuring a tree
is a visit.  It is a string (`2026-11-13Z`, a date in UTC), so this reads
MAX over it with a statistics query and parses it; 2026-11-13 was the newest
when the city was wired.  An edit made later on the same day as the last
build waits for the next visit; a new site is a visit, so it does not.

An outage degrades through `get_json_with_retry`; a layer that no longer has
the column, or answers MAX with something that is not that date, raises.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer
from shared.ingest import emit_freshness, get_json_with_retry

LAYER = FeatureLayer(
    "https://services.arcgis.com/HfwHS0BxZBQ1E5DY/arcgis/rest/services/"
    "TreeLocations_mso/FeatureServer/0",
    timeout=60,
)
FIELD = "inventory_date"


def fetch_modified_at() -> datetime:
    payload = get_json_with_retry(
        LAYER.query_url,
        params={
            "where": "1=1",
            "outStatistics": json.dumps(
                [{"statisticType": "max", "onStatisticField": FIELD,
                  "outStatisticFieldName": "max_value"}]
            ),
            "f": "json",
        },
        timeout=LAYER.timeout,
    )
    features = payload.get("features") or []
    if not features:
        raise RuntimeError(f"no statistics row for MAX({FIELD}) on {LAYER.base}")
    attrs = features[0].get("attributes") or {}
    raw = next((v for k, v in attrs.items() if k.lower() == "max_value"), None)
    try:
        day = datetime.strptime(str(raw).rstrip("Z"), "%Y-%m-%d")
    except ValueError as exc:
        raise RuntimeError(f"MAX({FIELD}) is not a date: {raw!r}") from exc
    return day.replace(tzinfo=timezone.utc)


if __name__ == "__main__":
    emit_freshness("USMSO", fetch_modified_at)

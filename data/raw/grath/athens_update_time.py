#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

"""
Fetch the last-modified timestamp for the Athens National Garden tree dataset
from the city's CKAN catalog — a ~10 KB metadata call instead of the full WFS
export just to check freshness.

API: GET https://opendata.cityofathens.gr/api/3/action/package_show?id=<dataset>
Reads: .result.metadata_modified  (naive ISO 8601, UTC)
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness, get_json_with_retry

METADATA_URL = (
    "https://opendata.cityofathens.gr/api/3/action/package_show"
    "?id=gis-athens-8303d4c8-371b-11ec-b388-0242ac120009"
)


def fetch_modified_at() -> datetime:
    data = get_json_with_retry(METADATA_URL)

    ts = data.get("result", {}).get("metadata_modified")
    if ts is None:
        raise RuntimeError("Dataset metadata missing result.metadata_modified")

    parsed = datetime.fromisoformat(ts)
    if parsed.tzinfo is None:
        # CKAN reports metadata_modified as naive UTC.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("GRATH", fetch_modified_at)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

"""
Freshness probe for the Berlin street tree dataset.

Uses the GeoNetwork metadata API to retrieve the dataset's last-modified
timestamp — a lightweight JSON response vs. downloading the full WFS dataset.

Metadata URL:
  https://gdi.berlin.de/geonetwork/srv/api/records/3368004a-d596-336a-8fdf-c4391f3313dd
Timestamp field: gmd:dateStamp → gco:DateTime → #text  (ISO 8601, UTC)
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness, get_json_with_retry

METADATA_URL = (
    "https://gdi.berlin.de/geonetwork/srv/api/records/"
    "3368004a-d596-336a-8fdf-c4391f3313dd"
)


def fetch_modified_at() -> datetime:
    # Berlin's whole geodata platform (this API, the WFS, and FIS-Broker) answers
    # every path with an HTTP 200 "Wartungsarbeiten" HTML page during
    # maintenance, so JSON decoding is part of the availability check, not a
    # detail of parsing — get_json_with_retry retries it and reports what came
    # back instead of a bare JSONDecodeError character offset.
    data = get_json_with_retry(METADATA_URL, headers={"Accept": "application/json"})
    # Path: gmd:dateStamp → gco:DateTime → #text  e.g. "2025-11-19T00:00:00Z"
    ts_str = data["gmd:dateStamp"]["gco:DateTime"]["#text"]
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("DEBER", fetch_modified_at)

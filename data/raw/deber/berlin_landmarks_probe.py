#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

"""
Freshness probe for Berlin landmarks.

Hits Overpass's `/api/timestamp` endpoint, which returns the OSM database
snapshot timestamp as a single ISO-8601 line. This is the same value as
`osm3s.timestamp_osm_base` on a query response, but with no query body and
no rate-limit slot consumed.
"""

import sys
from pathlib import Path
from datetime import datetime, timezone
import pyarrow as pa
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit, get_with_retry, OVERPASS_HEADERS

OVERPASS_TIMESTAMP_URL = "https://overpass-api.de/api/timestamp"


def fetch_modified_at() -> datetime:
    r = get_with_retry(OVERPASS_TIMESTAMP_URL, timeout=30, headers=OVERPASS_HEADERS)
    ts = r.text.strip()
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


if __name__ == "__main__":
    emit(
        pa.table(
            {
                "city": pa.array(["DEBER"], type=pa.string()),
                "data_updated_through": pa.array(
                    [fetch_modified_at()], type=pa.timestamp("us", tz="UTC")
                ),
            }
        )
    )

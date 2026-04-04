#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

"""
Freshness probe for Amsterdam tree data.

Fetches the most-recently-modified stamgegevens record and reads its
mutatieDatum field — a single lightweight API call vs. downloading the
full dataset.

API: GET https://api.data.amsterdam.nl/v1/bomen/stamgegevens/
     ?_format=json&page_size=1&_sort=-mutatieDatum
Reads: ._embedded.stamgegevens[0].mutatieDatum  (ISO 8601 string)
"""

import sys
from pathlib import Path
import pyarrow as pa
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import get_with_retry

PROBE_URL = (
    "https://api.data.amsterdam.nl/v1/bomen/stamgegevens/"
    "?_format=json&page_size=1&_sort=-mutatieDatum"
)


def fetch_modified_at() -> datetime:
    r = get_with_retry(PROBE_URL)
    data = r.json()

    rows = data.get("_embedded", {}).get("stamgegevens", [])
    if not rows:
        raise RuntimeError("No stamgegevens records returned from Amsterdam API")

    ts = rows[0].get("mutatieDatum")
    if not ts:
        raise RuntimeError("mutatieDatum missing from Amsterdam stamgegevens record")

    return datetime.fromisoformat(ts).astimezone(timezone.utc)


def emit(updated_at: datetime) -> None:
    table = pa.table(
        {
            "city": pa.array(["NLAMS"], type=pa.string()),
            "data_updated_through": pa.array(
                [updated_at], type=pa.timestamp("us", tz="UTC")
            ),
        }
    )
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == "__main__":
    emit(fetch_modified_at())

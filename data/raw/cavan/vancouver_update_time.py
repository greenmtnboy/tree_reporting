#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

"""
Fetch the last-modified timestamp for the Vancouver public trees dataset from the
OpenDataSoft catalog metadata API — a single lightweight call that avoids
downloading the full dataset just to check freshness.

API: GET https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets/public-trees
Reads: .metas.default.modified  (ISO 8601 string, e.g. "2026-03-30T13:50:58+00:00")
"""

import sys
from pathlib import Path
import pyarrow as pa
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import get_with_retry

METADATA_URL = (
    "https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets/public-trees"
)


def fetch_modified_at() -> datetime:
    r = get_with_retry(METADATA_URL)
    meta = r.json()

    ts = meta.get("metas", {}).get("default", {}).get("modified")
    if ts is None:
        raise RuntimeError("Dataset metadata missing metas.default.modified")

    return datetime.fromisoformat(ts).astimezone(timezone.utc)


def emit(updated_at: datetime) -> None:
    table = pa.table(
        {
            "city": pa.array(["CAVAN"], type=pa.string()),
            "data_updated_through": pa.array([updated_at], type=pa.timestamp("us", tz="UTC")),
        }
    )
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == "__main__":
    emit(fetch_modified_at())

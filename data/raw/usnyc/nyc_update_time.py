#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

import sys
from pathlib import Path
import pyarrow as pa
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import get_with_retry

DATASET_ID = "hn5i-inap"
METADATA_URL = f"https://data.cityofnewyork.us/api/views/{DATASET_ID}.json"


def fetch_rows_updated_at() -> datetime:
    r = get_with_retry(METADATA_URL)
    meta = r.json()

    ts = meta.get("rowsUpdatedAt")
    if ts is None:
        raise RuntimeError("Dataset metadata missing rowsUpdatedAt")

    return datetime.fromtimestamp(ts, tz=timezone.utc)


def emit(updated_at: datetime) -> None:
    table = pa.table(
        {
            "city": pa.array(["USNYC"], type=pa.string()),
            "data_updated_through": pa.array([updated_at], type=pa.timestamp("us", tz="UTC")),
        }
    )
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == "__main__":
    emit(fetch_rows_updated_at())

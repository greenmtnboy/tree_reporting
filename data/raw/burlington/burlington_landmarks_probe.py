#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow"]
# ///
"""Freshness probe for Burlington VT landmarks.

Emits the mtime of burlington_landmarks.csv as the freshness timestamp.
If the CSV does not exist, emits Unix epoch so Trilogy treats the
parquet as stale and rebuilds it (producing an empty dataset).
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
import pyarrow as pa

CSV_PATH = Path(__file__).parent / "burlington_landmarks.csv"


def modified_at() -> datetime:
    if not CSV_PATH.exists():
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return datetime.fromtimestamp(CSV_PATH.stat().st_mtime, tz=timezone.utc)


def emit(updated_at: datetime) -> None:
    table = pa.table(
        {
            "city": pa.array(["USBTV"], type=pa.string()),
            "data_updated_through": pa.array([updated_at], type=pa.timestamp("us", tz="UTC")),
        }
    )
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == "__main__":
    emit(modified_at())

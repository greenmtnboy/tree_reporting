#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow"]
# ///

import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa

CSV_PATH = Path(__file__).parent / 'washingtondc_landmarks.csv'


def modified_at() -> datetime:
    if not CSV_PATH.exists():
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return datetime.fromtimestamp(CSV_PATH.stat().st_mtime, tz=timezone.utc)


def emit(updated_at: datetime) -> None:
    table = pa.table({
        'city': pa.array(['USWAS'], type=pa.string()),
        'data_updated_through': pa.array([updated_at], type=pa.timestamp('us', tz='UTC')),
    })
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == '__main__':
    emit(modified_at())

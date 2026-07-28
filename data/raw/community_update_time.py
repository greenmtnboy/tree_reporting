#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

"""Emit the newest approved-tree timestamp for Trilogy freshness checks."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent))
from community_tree_info import load_published_records

EMPTY_DATASET_TIMESTAMP = datetime(1970, 1, 1, tzinfo=timezone.utc)


def fetch_latest_published_at() -> datetime:
    values = [
        record.get("publishedAt")
        for record in load_published_records()
        if isinstance(record.get("publishedAt"), datetime)
    ]
    return max(values, default=EMPTY_DATASET_TIMESTAMP).astimezone(timezone.utc)


def emit_timestamp(updated_at: datetime) -> None:
    table = pa.table(
        {
            "data_updated_through": pa.array(
                [updated_at], type=pa.timestamp("us", tz="UTC")
            )
        }
    )
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == "__main__":
    emit_timestamp(fetch_latest_published_at())

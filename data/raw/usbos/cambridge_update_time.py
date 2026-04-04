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

# Socrata views endpoint — returns rowsUpdatedAt as a Unix timestamp
DATASET_ID = "82zb-7qc9"
METADATA_URL = f"https://data.cambridgema.gov/api/views/{DATASET_ID}.json"


def fetch_modified_at() -> datetime:
    r = get_with_retry(METADATA_URL)
    data = r.json()

    ts = data.get("rowsUpdatedAt")
    if ts is None:
        raise RuntimeError("rowsUpdatedAt missing from Socrata metadata response")

    return datetime.fromtimestamp(ts, tz=timezone.utc)


def emit(updated_at: datetime) -> None:
    table = pa.table(
        {
            "city": pa.array(["USBOS"], type=pa.string()),
            "usbos_source": pa.array(["CAMBRIDGE"], type=pa.string()),
            "data_updated_through": pa.array([updated_at], type=pa.timestamp("us", tz="UTC")),
        }
    )
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == "__main__":
    emit(fetch_modified_at())

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import requests

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit

DATASET_ID = "rzic-39gi"
METADATA_URL = f"https://data.sfgov.org/api/views/{DATASET_ID}.json"


def fetch_rows_updated_at() -> datetime:
    r = requests.get(METADATA_URL)
    r.raise_for_status()
    meta = r.json()

    ts = meta.get("rowsUpdatedAt")
    if ts is None:
        raise RuntimeError("Dataset metadata missing rowsUpdatedAt")

    return datetime.fromtimestamp(ts, tz=timezone.utc)


if __name__ == "__main__":
    emit(
        pa.table(
            {
                "city": pa.array(["USSFO"], type=pa.string()),
                "data_updated_through": pa.array(
                    [fetch_rows_updated_at()], type=pa.timestamp("us", tz="UTC")
                ),
            }
        )
    )

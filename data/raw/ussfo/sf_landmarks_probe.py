#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

from datetime import datetime, timezone
from pathlib import Path


import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness, get_json_with_retry

DATASET_ID = "rzic-39gi"
METADATA_URL = f"https://data.sfgov.org/api/views/{DATASET_ID}.json"


def fetch_rows_updated_at() -> datetime:
    meta = get_json_with_retry(METADATA_URL, timeout=30)

    ts = meta.get("rowsUpdatedAt")
    if ts is None:
        raise RuntimeError("Dataset metadata missing rowsUpdatedAt")

    return datetime.fromtimestamp(ts, tz=timezone.utc)


if __name__ == "__main__":
    emit_freshness("USSFO", fetch_rows_updated_at)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness, get_json_with_retry

# Socrata views endpoint — returns rowsUpdatedAt as a Unix timestamp
DATASET_ID = "82zb-7qc9"
METADATA_URL = f"https://data.cambridgema.gov/api/views/{DATASET_ID}.json"


def fetch_modified_at() -> datetime:
    data = get_json_with_retry(METADATA_URL)

    ts = data.get("rowsUpdatedAt")
    if ts is None:
        raise RuntimeError("rowsUpdatedAt missing from Socrata metadata response")

    return datetime.fromtimestamp(ts, tz=timezone.utc)


if __name__ == "__main__":
    emit_freshness("USBOS", fetch_modified_at)

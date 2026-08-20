#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

import sys
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness, get_json_with_retry

METADATA_URL = 'https://data.lacity.org/api/views/vt5t-mscf.json'


def fetch_modified_at() -> datetime:
    payload = get_json_with_retry(METADATA_URL)
    ts = payload.get('rowsUpdatedAt')
    if ts is None:
        raise RuntimeError('rowsUpdatedAt missing from Los Angeles metadata')
    return datetime.fromtimestamp(ts, tz=timezone.utc)


if __name__ == "__main__":
    emit_freshness("USLAX", fetch_modified_at)

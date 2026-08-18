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

METADATA_URL = 'https://data.buenosaires.gob.ar/api/3/action/package_show?id=arbolado-publico-lineal'


def fetch_modified_at() -> datetime:
    payload = get_json_with_retry(METADATA_URL)['result']
    ts = payload.get('metadata_modified')
    if not ts:
        raise RuntimeError('metadata_modified missing from Buenos Aires package metadata')
    return datetime.fromisoformat(ts).astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("ARBUE", fetch_modified_at)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import get_with_retry

METADATA_URL = 'https://data.buenosaires.gob.ar/api/3/action/package_show?id=arbolado-publico-lineal'


def fetch_modified_at() -> datetime:
    payload = get_with_retry(METADATA_URL).json()['result']
    ts = payload.get('metadata_modified')
    if not ts:
        raise RuntimeError('metadata_modified missing from Buenos Aires package metadata')
    return datetime.fromisoformat(ts).astimezone(timezone.utc)


def emit(updated_at: datetime) -> None:
    table = pa.table({
        'city': pa.array(['ARBUE'], type=pa.string()),
        'data_updated_through': pa.array([updated_at], type=pa.timestamp('us', tz='UTC')),
    })
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == '__main__':
    emit(fetch_modified_at())

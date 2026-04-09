#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests"]
# ///

import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import get_with_retry

STATS_URL = (
    'https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Urban_Tree_Canopy/MapServer/23/query'
    '?where=1%3D1'
    '&outStatistics=%5B%7B%22statisticType%22%3A%22max%22%2C%22onStatisticField%22%3A%22LAST_EDITED_DATE%22%2C%22outStatisticFieldName%22%3A%22max_edit%22%7D%5D'
    '&f=json'
)


def fetch_modified_at() -> datetime:
    payload = get_with_retry(STATS_URL).json()
    raw = payload['features'][0]['attributes'].get('MAX_EDIT')
    if raw is None:
        raise RuntimeError('MAX_EDIT missing from Washington DC statistics response')
    return datetime.fromtimestamp(raw / 1000, tz=timezone.utc)


def emit(updated_at: datetime) -> None:
    table = pa.table({
        'city': pa.array(['USWAS'], type=pa.string()),
        'data_updated_through': pa.array([updated_at], type=pa.timestamp('us', tz='UTC')),
    })
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == '__main__':
    emit(fetch_modified_at())

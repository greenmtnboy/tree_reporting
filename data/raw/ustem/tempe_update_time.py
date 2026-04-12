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

METADATA_URL = 'https://services.arcgis.com/lQySeXwbBg53XWDi/arcgis/rest/services/TempeGuadalupe_CanopyCover2019_TreeInv_2021_Tempe_iTreeResults_TempeAZOct_2021_1/FeatureServer/0?f=json'


def fetch_modified_at() -> datetime:
    payload = get_with_retry(METADATA_URL).json()
    editing = payload.get('editingInfo', {})
    raw = editing.get('dataLastEditDate') or editing.get('lastEditDate')
    if raw is None:
        raise RuntimeError('editingInfo.dataLastEditDate missing from Tempe metadata')
    return datetime.fromtimestamp(raw / 1000, tz=timezone.utc)


def emit(updated_at: datetime) -> None:
    table = pa.table({
        'city': pa.array(['USTEM'], type=pa.string()),
        'data_updated_through': pa.array([updated_at], type=pa.timestamp('us', tz='UTC')),
    })
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == '__main__':
    emit(fetch_modified_at())

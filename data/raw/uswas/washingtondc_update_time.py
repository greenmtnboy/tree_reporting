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

STATS_URL = (
    'https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Urban_Tree_Canopy/MapServer/23/query'
    '?where=1%3D1'
    '&outStatistics=%5B%7B%22statisticType%22%3A%22max%22%2C%22onStatisticField%22%3A%22LAST_EDITED_DATE%22%2C%22outStatisticFieldName%22%3A%22max_edit%22%7D%5D'
    '&f=json'
)


def fetch_modified_at() -> datetime:
    payload = get_json_with_retry(STATS_URL)
    raw = payload['features'][0]['attributes'].get('MAX_EDIT')
    if raw is None:
        raise RuntimeError('MAX_EDIT missing from Washington DC statistics response')
    return datetime.fromtimestamp(raw / 1000, tz=timezone.utc)


if __name__ == "__main__":
    emit_freshness("USWAS", fetch_modified_at)

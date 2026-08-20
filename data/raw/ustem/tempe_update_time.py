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

METADATA_URL = 'https://services.arcgis.com/lQySeXwbBg53XWDi/arcgis/rest/services/TempeGuadalupe_CanopyCover2019_TreeInv_2021_Tempe_iTreeResults_TempeAZOct_2021_1/FeatureServer/0?f=json'


def fetch_modified_at() -> datetime:
    payload = get_json_with_retry(METADATA_URL)
    editing = payload.get('editingInfo', {})
    raw = editing.get('dataLastEditDate') or editing.get('lastEditDate')
    if raw is None:
        raise RuntimeError('editingInfo.dataLastEditDate missing from Tempe metadata')
    return datetime.fromtimestamp(raw / 1000, tz=timezone.utc)


if __name__ == "__main__":
    emit_freshness("USTEM", fetch_modified_at)

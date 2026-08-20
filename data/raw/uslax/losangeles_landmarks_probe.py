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

LAYER_URL = (
    "https://services5.arcgis.com/7nsPwEMP38bSkCjy/ArcGIS/rest/services/"
    "Historic_Cultural_Monuments/FeatureServer/4"
)


def fetch_modified_at() -> datetime:
    data = get_json_with_retry(LAYER_URL + "?f=json")
    ms = data.get("editingInfo", {}).get("dataLastEditDate")
    if ms is None:
        raise RuntimeError("dataLastEditDate missing from ArcGIS layer metadata")
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


if __name__ == "__main__":
    emit_freshness("USLAX", fetch_modified_at)

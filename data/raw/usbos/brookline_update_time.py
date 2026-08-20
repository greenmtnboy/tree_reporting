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

# ArcGIS FeatureServer layer metadata — editingInfo.dataLastEditDate is ms since epoch
LAYER_URL = "https://services1.arcgis.com/Oknk0tvfHOElpgGU/arcgis/rest/services/Brookline_Tree_Viewer_Web_WFL1/FeatureServer/0"


def fetch_modified_at() -> datetime:
    data = get_json_with_retry(LAYER_URL + "?f=json")

    ms = data.get("editingInfo", {}).get("dataLastEditDate")
    if ms is None:
        raise RuntimeError("dataLastEditDate missing from ArcGIS layer metadata")

    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


if __name__ == "__main__":
    emit_freshness("USBOS", fetch_modified_at)

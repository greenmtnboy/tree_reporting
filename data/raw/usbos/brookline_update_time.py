#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests"]
# ///

import sys
import requests
import pyarrow as pa
from datetime import datetime, timezone

# ArcGIS FeatureServer layer metadata — editingInfo.dataLastEditDate is ms since epoch
LAYER_URL = "https://services1.arcgis.com/Oknk0tvfHOElpgGU/arcgis/rest/services/Brookline_Tree_Viewer_Web_WFL1/FeatureServer/0"


def fetch_modified_at() -> datetime:
    r = requests.get(LAYER_URL, params={"f": "json"}, timeout=30)
    r.raise_for_status()
    data = r.json()

    ms = data.get("editingInfo", {}).get("dataLastEditDate")
    if ms is None:
        raise RuntimeError("dataLastEditDate missing from ArcGIS layer metadata")

    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def emit(updated_at: datetime) -> None:
    table = pa.table(
        {
            "city": pa.array(["USBOS"], type=pa.string()),
            "usbos_source": pa.array(["BROOKLINE"], type=pa.string()),
            "data_updated_through": pa.array([updated_at], type=pa.timestamp("us", tz="UTC")),
        }
    )
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == "__main__":
    emit(fetch_modified_at())

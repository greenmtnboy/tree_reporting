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

LAYER_URL = (
    "https://services5.arcgis.com/7nsPwEMP38bSkCjy/ArcGIS/rest/services/"
    "Historic_Cultural_Monuments/FeatureServer/4"
)


def fetch_modified_at() -> datetime:
    response = get_with_retry(LAYER_URL + "?f=json")
    data = response.json()
    ms = data.get("editingInfo", {}).get("dataLastEditDate")
    if ms is None:
        raise RuntimeError("dataLastEditDate missing from ArcGIS layer metadata")
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def emit(updated_at: datetime) -> None:
    table = pa.table(
        {
            "city": pa.array(["USLAX"], type=pa.string()),
            "data_updated_through": pa.array(
                [updated_at], type=pa.timestamp("us", tz="UTC")
            ),
        }
    )
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == "__main__":
    emit(fetch_modified_at())

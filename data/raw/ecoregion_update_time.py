#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

import sys
from datetime import datetime, timezone

import pyarrow as pa

from _ecoregion_shared import LAYER_METADATA_URL
from _ingest_shared import get_with_retry


def fetch_data_updated_through() -> datetime:
    response = get_with_retry(LAYER_METADATA_URL)
    payload = response.json()

    editing_info = payload.get("editingInfo", {})
    ts_ms = editing_info.get("dataLastEditDate") or editing_info.get("lastEditDate")
    if ts_ms is None:
        raise RuntimeError("ArcGIS metadata missing editingInfo.dataLastEditDate")
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


def emit(updated_at: datetime) -> None:
    table = pa.table(
        {
            "data_updated_through": pa.array(
                [updated_at],
                type=pa.timestamp("us", tz="UTC"),
            ),
        }
    )
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == "__main__":
    emit(fetch_data_updated_through())

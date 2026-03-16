#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests"]
# ///

import sys
import requests
import pyarrow as pa
from datetime import datetime, timezone

RESOURCE_ID = "fb53d967-ead6-4b4e-ab17-506521434038"
METADATA_URL = f"https://data.boston.gov/api/3/action/resource_show?id={RESOURCE_ID}"


def fetch_rows_updated_at() -> datetime:
    r = requests.get(METADATA_URL)
    r.raise_for_status()
    meta = r.json()

    result = meta.get("result", {})
    ts = result.get("last_modified") or result.get("created")
    if ts is None:
        raise RuntimeError("Dataset metadata missing last_modified")

    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def emit(updated_at: datetime) -> None:
    table = pa.table(
        {
            "city": pa.array(["USBOS"], type=pa.string()),
            "data_updated_through": pa.array([updated_at], type=pa.timestamp("us", tz="UTC")),
        }
    )
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == "__main__":
    emit(fetch_rows_updated_at())

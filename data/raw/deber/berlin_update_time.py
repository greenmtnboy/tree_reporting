#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests"]
# ///

"""
Freshness probe for the Berlin street tree dataset.

Uses the GeoNetwork metadata API to retrieve the dataset's last-modified
timestamp — a lightweight JSON response vs. downloading the full WFS dataset.

Metadata URL:
  https://gdi.berlin.de/geonetwork/srv/api/records/3368004a-d596-336a-8fdf-c4391f3313dd
Timestamp field: gmd:dateStamp → gco:DateTime → #text  (ISO 8601, UTC)
"""

import sys
from datetime import datetime, timezone

import pyarrow as pa
import requests

METADATA_URL = (
    "https://gdi.berlin.de/geonetwork/srv/api/records/"
    "3368004a-d596-336a-8fdf-c4391f3313dd"
)


def fetch_modified_at() -> datetime:
    r = requests.get(
        METADATA_URL,
        headers={"Accept": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    # Path: gmd:dateStamp → gco:DateTime → #text  e.g. "2025-11-19T00:00:00Z"
    ts_str = data["gmd:dateStamp"]["gco:DateTime"]["#text"]
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc)


def emit(updated_at: datetime) -> None:
    table = pa.table(
        {
            "city": pa.array(["DEBER"], type=pa.string()),
            "data_updated_through": pa.array(
                [updated_at], type=pa.timestamp("us", tz="UTC")
            ),
        }
    )
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == "__main__":
    emit(fetch_modified_at())

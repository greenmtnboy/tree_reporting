#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests"]
# ///

"""
Freshness probe for Paris landmarks.
Fetches dataset metadata from data.iledefrance.fr and emits the last-modified
timestamp as a single-row Arrow table.
"""

import sys
import requests
import pyarrow as pa
from datetime import datetime, timezone

METADATA_URL = (
    "https://data.iledefrance.fr/api/explore/v2.1/catalog/datasets/"
    "immeubles-proteges-au-titre-des-monuments-historiques"
)


def fetch_last_modified() -> datetime:
    r = requests.get(METADATA_URL, timeout=30)
    r.raise_for_status()
    meta = r.json()
    ts = meta.get("metas", {}).get("default", {}).get("modified")
    if ts is None:
        raise RuntimeError("Dataset metadata missing metas.default.modified")
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def emit(updated_at: datetime) -> None:
    table = pa.table(
        {
            "city": pa.array(["FRPAR"], type=pa.string()),
            "data_updated_through": pa.array(
                [updated_at], type=pa.timestamp("us", tz="UTC")
            ),
        }
    )
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == "__main__":
    emit(fetch_last_modified())

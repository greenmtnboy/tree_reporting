#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests"]
# ///

"""
Freshness probe for Melbourne landmarks.

Fetches dataset metadata from the City of Melbourne Open Data portal (OpenDataSoft v2)
and emits the last-modified timestamp.
"""

import sys
import requests
import pyarrow as pa
from datetime import datetime, timezone

DATASET_ID = (
    "landmarks-and-places-of-interest-including-schools-theatres-health-services-spor"
)
METADATA_URL = (
    f"https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/{DATASET_ID}"
)


def fetch_modified_at() -> datetime:
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
            "city": pa.array(["AUMEL"], type=pa.string()),
            "data_updated_through": pa.array(
                [updated_at], type=pa.timestamp("us", tz="UTC")
            ),
        }
    )
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == "__main__":
    emit(fetch_modified_at())

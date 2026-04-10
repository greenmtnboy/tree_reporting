#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

"""
Freshness probe for Vancouver heritage sites.
Fetches dataset metadata from opendata.vancouver.ca and emits the last-modified
timestamp as a single-row Arrow table.

API: GET https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets/heritage-sites
Reads: .metas.default.modified  (ISO 8601 string)
"""

from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import requests

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit

METADATA_URL = (
    "https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets/heritage-sites"
)


def fetch_last_modified() -> datetime:
    r = requests.get(METADATA_URL, timeout=30)
    r.raise_for_status()
    meta = r.json()
    ts = meta.get("metas", {}).get("default", {}).get("modified")
    if ts is None:
        raise RuntimeError("Dataset metadata missing metas.default.modified")
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


if __name__ == "__main__":
    emit(
        pa.table(
            {
                "city": pa.array(["CAVAN"], type=pa.string()),
                "data_updated_through": pa.array(
                    [fetch_last_modified()], type=pa.timestamp("us", tz="UTC")
                ),
            }
        )
    )

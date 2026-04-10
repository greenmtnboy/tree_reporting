#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

"""
Freshness probe for Melbourne landmarks.

Fetches dataset metadata from the City of Melbourne Open Data portal (OpenDataSoft v2)
and emits the last-modified timestamp.
"""

from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import requests

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit

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


if __name__ == "__main__":
    emit(
        pa.table(
            {
                "city": pa.array(["AUMEL"], type=pa.string()),
                "data_updated_through": pa.array(
                    [fetch_modified_at()], type=pa.timestamp("us", tz="UTC")
                ),
            }
        )
    )

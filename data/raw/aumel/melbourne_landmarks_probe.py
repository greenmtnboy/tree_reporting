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


import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness, get_json_with_retry

DATASET_ID = (
    "landmarks-and-places-of-interest-including-schools-theatres-health-services-spor"
)
METADATA_URL = (
    f"https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/{DATASET_ID}"
)


def fetch_modified_at() -> datetime:
    meta = get_json_with_retry(METADATA_URL, timeout=30)
    ts = meta.get("metas", {}).get("default", {}).get("modified")
    if ts is None:
        raise RuntimeError("Dataset metadata missing metas.default.modified")
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("AUMEL", fetch_modified_at)

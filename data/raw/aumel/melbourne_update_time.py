#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

"""
Fetch the last-modified timestamp for the Melbourne Urban Forest trees dataset
from the OpenDataSoft catalog metadata API — a single lightweight call that
avoids downloading the full dataset just to check freshness.

API: GET https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/trees-with-species-and-dimensions-urban-forest
Reads: .metas.default.modified  (ISO 8601 string)
"""

import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness, get_json_with_retry

METADATA_URL = (
    "https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/"
    "trees-with-species-and-dimensions-urban-forest"
)


def fetch_modified_at() -> datetime:
    meta = get_json_with_retry(METADATA_URL)

    ts = meta.get("metas", {}).get("default", {}).get("modified")
    if ts is None:
        raise RuntimeError("Dataset metadata missing metas.default.modified")

    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("AUMEL", fetch_modified_at)

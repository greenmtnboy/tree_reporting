#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

"""
Fetch the last-modified timestamp for the Paris tree dataset from the
OpenDataSoft catalog metadata API — a single lightweight call that avoids
downloading the full 217k-row dataset just to check freshness.

API: GET https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/les-arbres
Reads: .metas.default.modified  (ISO 8601 string, e.g. "2026-03-13T09:34:12+00:00")
"""

import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness, get_json_with_retry

METADATA_URL = (
    "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/les-arbres"
)


def fetch_modified_at() -> datetime:
    meta = get_json_with_retry(METADATA_URL)

    ts = meta.get("metas", {}).get("default", {}).get("modified")
    if ts is None:
        raise RuntimeError("Dataset metadata missing metas.default.modified")

    return datetime.fromisoformat(ts).astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("FRPAR", fetch_modified_at)

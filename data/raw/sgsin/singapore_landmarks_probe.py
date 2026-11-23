#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Singapore's National Monuments and Historic Sites.

The later of the two NHB datasets' `lastUpdatedAt` on data.gov.sg's v2
metadata endpoint (both 2026-04-16T10:06:4x+08:00 on 2026-11-22), read with
its offset and converted to UTC.  Kept in step with singapore_landmarks.py's
LAYERS.

An outage degrades through `get_json_with_retry`; a payload without
`lastUpdatedAt` raises, because that is data.gov.sg changing its metadata
shape.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry  # noqa: E402

DATASETS = (
    "d_b29c230ec6b609e29ed42f71ca9a8767",  # Monuments (GEOJSON)
    "d_31e16b12809e66673e90d8b04fdee1b2",  # Historic Sites (GEOJSON)
)
METADATA_URL = "https://api-production.data.gov.sg/v2/public/api/datasets/{dataset}/metadata"


def last_updated(dataset: str) -> datetime:
    url = METADATA_URL.format(dataset=dataset)
    payload = get_json_with_retry(url)
    data = payload.get("data") if isinstance(payload, dict) else None
    stamp = data.get("lastUpdatedAt") if isinstance(data, dict) else None
    if not stamp:
        raise RuntimeError(f"Singapore landmarks: no data.lastUpdatedAt in {url}")
    parsed = datetime.fromisoformat(stamp)
    if parsed.tzinfo is None:
        raise RuntimeError(f"Singapore landmarks: lastUpdatedAt {stamp!r} has no offset")
    return parsed.astimezone(timezone.utc)


def fetch_modified_at() -> datetime:
    return max(last_updated(d) for d in DATASETS)


if __name__ == "__main__":
    emit_freshness("SGSIN", fetch_modified_at, label="SGSIN landmarks")

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

"""
Freshness probe for Berlin landmarks.

Runs the same Overpass filter as berlin_landmarks.py but with `out ids meta`
(no geometry, no tags) to cheaply get the max element timestamp — the actual
date the most recently edited matching landmark was changed in OSM.
"""

import sys
from pathlib import Path
from datetime import datetime, timezone
import pyarrow as pa
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit, post_with_retry

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

QUERY = """
[out:json][timeout:60];
(
  node["historic"]["name"](52.3,13.0,52.7,13.8);
  way["historic"]["name"](52.3,13.0,52.7,13.8);
);
out ids meta;
"""


def fetch_modified_at() -> datetime:
    r = post_with_retry(OVERPASS_URL, data={"data": QUERY}, timeout=90)
    elements = r.json().get("elements", [])
    timestamps = [e["timestamp"] for e in elements if "timestamp" in e]
    if not timestamps:
        return datetime.now(tz=timezone.utc)
    latest = max(timestamps)
    return datetime.fromisoformat(latest.replace("Z", "+00:00")).astimezone(timezone.utc)


if __name__ == "__main__":
    emit(
        pa.table(
            {
                "city": pa.array(["DEBER"], type=pa.string()),
                "data_updated_through": pa.array(
                    [fetch_modified_at()], type=pa.timestamp("us", tz="UTC")
                ),
            }
        )
    )

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

"""
Freshness probe for London landmarks.

Runs the same Overpass filter as london_landmarks.py but with `out ids meta`
(no geometry, no tags) to cheaply get the max element timestamp — the actual
date the most recently edited matching landmark was changed in OSM.
"""

import sys
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import OVERPASS_HEADERS, emit_freshness, post_json_with_retry

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

QUERY = """
[out:json][timeout:60];
(
  node["heritage:operator"="Historic England"](51.28,-0.56,51.70,0.35);
  way["heritage:operator"="Historic England"](51.28,-0.56,51.70,0.35);
  relation["heritage:operator"="Historic England"](51.28,-0.56,51.70,0.35);
);
out ids meta;
"""


def fetch_modified_at() -> datetime:
    payload = post_json_with_retry(
        OVERPASS_URL, data={"data": QUERY}, timeout=90, headers=OVERPASS_HEADERS
    )
    elements = payload.get("elements", [])
    timestamps = [e["timestamp"] for e in elements if "timestamp" in e]
    if not timestamps:
        return datetime.now(tz=timezone.utc)
    latest = max(timestamps)
    return datetime.fromisoformat(latest.replace("Z", "+00:00")).astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("GBLON", fetch_modified_at)

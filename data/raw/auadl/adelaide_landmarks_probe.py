#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Adelaide's heritage places.

The download zip's own `Last-Modified` (2026-06-28 when this was written),
from one HEAD.  The CKAN resource record for the GeoJSON is no use: its
`last_modified` says 2023-07-15 while the file behind it was republished in
2026, so the catalogue stamp would freeze the city's landmarks.

A response with no `Last-Modified` raises: that is the publisher changing how
the file is served, not an outage, and must not read as "no new data".
"""

import sys
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, head_with_retry

# Kept in step with adelaide_landmarks.py's SOURCE_URL.
SOURCE_URL = "https://www.dptiapps.com.au/dataportal/SAHeritagePlaces_geojson.zip"


def fetch_modified_at() -> datetime:
    response = head_with_retry(SOURCE_URL, timeout=60)
    stamp = response.headers.get("Last-Modified")
    if not stamp:
        raise RuntimeError(f"no Last-Modified header on {SOURCE_URL}")
    return parsedate_to_datetime(stamp)


if __name__ == "__main__":
    emit_freshness("AUADL", fetch_modified_at, label="AUADL landmarks")

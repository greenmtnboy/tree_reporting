#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

"""
Freshness probe for Berlin landmarks.

Hits Overpass's `/api/timestamp` endpoint, which returns the OSM database
snapshot timestamp as a single ISO-8601 line. This is the same value as
`osm3s.timestamp_osm_base` on a query response, but with no query body and
no rate-limit slot consumed.
"""

import sys
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    OVERPASS_HEADERS,
    UpstreamUnavailable,
    emit_freshness,
    get_with_retry,
)

OVERPASS_TIMESTAMP_URL = "https://overpass-api.de/api/timestamp"


def fetch_modified_at() -> datetime:
    r = get_with_retry(OVERPASS_TIMESTAMP_URL, timeout=30, headers=OVERPASS_HEADERS)
    ts = r.text.strip()
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError as e:
        # A busy Overpass instance answers 200 with an HTML "too many requests"
        # page rather than the one-line timestamp; that is an outage, not a
        # schema change, so it degrades instead of failing the whole refresh.
        raise UpstreamUnavailable(
            f"{OVERPASS_TIMESTAMP_URL} returned a body that is not an ISO "
            f"timestamp: {ts[:160]!r} ({e})"
        ) from e


if __name__ == "__main__":
    emit_freshness("DEBER", fetch_modified_at)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for the scheduled OSM extract jobs: Overpass's osm_base time.

GET https://overpass-api.de/api/timestamp returns one bare ISO-8601 line — the
time the OSM data Overpass serves is current through.  It advances roughly
every minute, which is exactly why the *main* refresh must never use it (the
watermark that made Boston and Tempe rebuild three times a day) and exactly
right for a dedicated extract job: every scheduled firing sees a newer
timestamp, goes stale, and re-extracts — so the job's cron *is* the extraction
cadence, and the staging parquet records the osm_base time it captured.

Availability degrades, parse failures do not: an unreachable or overloaded
Overpass (which answers with HTTP 200 carrying an HTML page rather than a
timestamp) raises UpstreamUnavailable, so emit_freshness reports the epoch,
the staging parquet compares fresh, and the job no-ops until the next firing.
A 2xx body that is neither HTML nor a parseable timestamp means the endpoint
changed shape, and that keeps raising loudly.

City-agnostic on purpose — the timestamp is global, so every extract preql
maps this one script onto its own freshness property.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "raw"))
from _ingest_shared import (  # noqa: E402
    OVERPASS_HEADERS,
    UpstreamUnavailable,
    emit_freshness,
    get_with_retry,
)

TIMESTAMP_URL = "https://overpass-api.de/api/timestamp"


def fetch_osm_base() -> datetime:
    response = get_with_retry(TIMESTAMP_URL, timeout=30, headers=OVERPASS_HEADERS)
    body = response.text.strip()
    if not body or body.startswith("<"):
        # An overloaded instance serves an HTML page with HTTP 200.
        raise UpstreamUnavailable(
            f"{TIMESTAMP_URL} returned a non-timestamp body: {body[:80]!r}"
        )
    return datetime.fromisoformat(body.replace("Z", "+00:00")).astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness(None, fetch_osm_base, label="Overpass osm_base timestamp")

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Berlin landmarks.

Emits the mtime of deber_landmarks_staging.parquet.  Extraction is decoupled
from refresh -- berlin_landmarks_extract.py queries Overpass and commits
the staging file, and this probe never touches the network -- so the moment the
published Parquet becomes stale is the moment that file changes.

Two reasons, both the same ones the OSM tree extracts already follow (see
EXTENDING.md).  Overpass allows two concurrent slots per client IP and answers
an over-budget request with a 200 carrying an HTML page or a `runtime error`
remark; with three Overpass callers reachable at refresh time and
`parallelism = 3`, a full refresh could throttle itself and fail a city on a
transient.  And the only cheap OSM-wide watermark is the database timestamp,
which advances every minute and would mark the city stale on every tick.

A missing staging file emits the epoch (the city sits out this run) rather than
raising: one absent optional source must never abort the whole refresh.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness  # noqa: E402

STAGING_PATH = Path(__file__).parent / "deber_landmarks_staging.parquet"


def modified_at() -> datetime:
    if not STAGING_PATH.exists():
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return datetime.fromtimestamp(STAGING_PATH.stat().st_mtime, tz=timezone.utc)


if __name__ == "__main__":
    emit_freshness("DEBER", modified_at, label="DEBER landmarks staging")

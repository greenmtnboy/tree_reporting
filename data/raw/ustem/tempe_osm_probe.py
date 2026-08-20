#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Tempe's OSM staging parquet.

Emits the mtime of ustem_osm_staging.parquet: the staging file only changes
when tempe_osm_extract.py is re-run and the result committed, so that is the
moment the city's published Parquet becomes stale.  A missing staging file
emits the epoch (sits out the run) rather than raising — one absent optional
source must never abort the whole refresh.
"""

from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness

STAGING_PATH = Path(__file__).parent / "ustem_osm_staging.parquet"


def modified_at() -> datetime:
    if not STAGING_PATH.exists():
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return datetime.fromtimestamp(STAGING_PATH.stat().st_mtime, tz=timezone.utc)


if __name__ == "__main__":
    emit_freshness("USTEM", modified_at, label="USTEM OSM staging")

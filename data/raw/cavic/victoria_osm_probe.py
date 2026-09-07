#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Victoria's OSM staging parquet.

Emits the GCS publication time of cavic_osm_staging.parquet, which only moves
when an extraction is republished -- that is the moment the city's Parquet
becomes stale.  Never Overpass's own `osm_base` timestamp: it advances every
minute and would rebuild the city on every tick.  A missing object emits the
epoch (sits out the run) rather than raising.
"""

from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness, staging_modified_at

STAGING_NAME = "cavic_osm_staging.parquet"


def modified_at() -> datetime:
    return staging_modified_at(STAGING_NAME)


if __name__ == "__main__":
    emit_freshness("CAVIC", modified_at, label="CAVIC OSM staging")

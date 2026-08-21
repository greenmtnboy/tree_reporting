#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Paris's OSM staging parquet.

Emits the GCS publication time of frpar_osm_staging.parquet, which only
changes when paris_osm_extract.py is re-run and its output uploaded.
Overpass is never contacted during a refresh.

Not a local mtime: git does not preserve it, so a committed staging file made
every fresh clone look like newly extracted data.  A missing object emits the
epoch, so an absent optional source sits out the run rather than aborting it.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness, staging_modified_at  # noqa: E402

STAGING_NAME = "frpar_osm_staging.parquet"


def modified_at() -> datetime:
    return staging_modified_at(STAGING_NAME)


if __name__ == "__main__":
    emit_freshness("FRPAR", modified_at, label="FRPAR OSM staging")

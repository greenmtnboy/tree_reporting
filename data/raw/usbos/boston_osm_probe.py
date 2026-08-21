#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Boston's OSM staging parquet.

Emits the GCS publication time of usbos_osm_staging.parquet.  The staged copy
only changes when boston_osm_extract.py is re-run and its output uploaded, so
that is the moment the city's published Parquet becomes stale.

The watermark is the object's Last-Modified rather than a local file mtime:
the staging parquets used to be committed, and git does not preserve mtime, so
every fresh clone stamped the checkout time and the city rebuilt on every tick.
A missing object emits the epoch (sits out the run) rather than raising -- one
absent optional source must never abort the whole refresh.
"""

from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness, staging_modified_at

STAGING_NAME = "usbos_osm_staging.parquet"


def modified_at() -> datetime:
    return staging_modified_at(STAGING_NAME)


if __name__ == "__main__":
    emit_freshness("USBOS", modified_at, label="USBOS OSM staging")

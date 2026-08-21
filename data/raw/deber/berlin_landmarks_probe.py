#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Berlin's staged Overpass landmarks.

Emits the GCS publication time of deber_landmarks_staging.parquet, which only
changes when berlin_landmarks_extract.py is re-run and its output uploaded.
Overpass is never contacted during a refresh.

The watermark is the object's Last-Modified rather than a local file mtime:
the staging parquets used to be committed, and git does not preserve mtime, so
every fresh clone stamped the checkout time.  A missing object emits the epoch.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness, staging_modified_at  # noqa: E402

STAGING_NAME = "deber_landmarks_staging.parquet"


def modified_at() -> datetime:
    return staging_modified_at(STAGING_NAME)


if __name__ == "__main__":
    emit_freshness("DEBER", modified_at, label="DEBER landmarks staging")

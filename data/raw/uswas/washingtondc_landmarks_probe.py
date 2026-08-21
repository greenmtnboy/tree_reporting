#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

from datetime import datetime
from pathlib import Path


import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness, staging_modified_at

STAGING_NAME = "washingtondc_landmarks.csv"


def modified_at() -> datetime:
    # The GCS object's publication time, not the local file's mtime.
    # The CSV is committed, and git does not preserve mtime, so a fresh
    # clone -- which is every cloud job run -- stamped the checkout time
    # and rebuilt this city's landmarks on every tick, for ever.
    return staging_modified_at(STAGING_NAME)


if __name__ == "__main__":
    emit_freshness("USWAS", modified_at)

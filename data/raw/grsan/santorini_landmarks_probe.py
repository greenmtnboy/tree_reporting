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

STAGING_NAME = "grsan_landmarks_staging.parquet"


def modified_at() -> datetime:
    # The GCS object's publication time, not the committed CSV's mtime (git
    # does not preserve mtime, so a fresh clone -- which is every cloud job
    # run -- would stamp the checkout time and rebuild on every tick).  The
    # object is published from the CSV by the ad-hoc `landmarks-grsan` cloud
    # job; firing it is what moves this watermark.
    return staging_modified_at(STAGING_NAME)


if __name__ == "__main__":
    emit_freshness("GRSAN", modified_at)

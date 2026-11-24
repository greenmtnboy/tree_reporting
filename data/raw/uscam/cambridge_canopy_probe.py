#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for the LIDAR canopy-height parquet Cambridge reads.

Emits the GCS publication time of the Boston metro's table
(`height_staging_name` resolves Cambridge to it), which moves only when the
`canopy-usbos` job publishes a rebuilt one.  Folded into the city's published
watermark so a new table re-heights every tree.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.canopy import height_staging_name  # noqa: E402
from shared.ingest import emit_freshness, staging_modified_at  # noqa: E402

STAGING_NAME = height_staging_name("USCAM")


def modified_at() -> datetime:
    return staging_modified_at(STAGING_NAME)


if __name__ == "__main__":
    emit_freshness("USCAM", modified_at, label="USCAM canopy staging")

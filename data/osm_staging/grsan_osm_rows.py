#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Santorini's OSM trees as a datasource script for the scheduled extract job.

Emits the same normalised rows as raw/grsan/santorini_osm_extract.py, but to
stdout instead of uploading — the job's `trilogy refresh` materialises them
to the staging parquet with its own GCS credentials (see
osm_staging/grsan_osm_staging.preql).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "raw"))
from _osm_shared import stage_city_rows  # noqa: E402

if __name__ == "__main__":
    stage_city_rows("GRSAN", "Santorini OSM")

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Burlington's Overture position-lookup parquet.

Emits the GCS publication time of usbtv_overture_snap.parquet, which only
moves when the `overture-usbtv` job (or burlington_overture_extract.py)
publishes a table built from a new Overture release.  Folded into the city's
published watermark, so a new lookup rebuilds the tree parquet and every
tree that now sits in a building or a road is moved.  A missing object
emits the epoch: a city whose lookup has not been staged yet refreshes
without one rather than failing, and its lookup join simply matches nothing
-- except that the model reads the object by URL, so the first refresh
after wiring still needs the object to exist.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, staging_modified_at  # noqa: E402
from shared.overture import snap_staging_name  # noqa: E402

STAGING_NAME = snap_staging_name("USBTV")


def modified_at() -> datetime:
    return staging_modified_at(STAGING_NAME)


if __name__ == "__main__":
    emit_freshness("USBTV", modified_at, label="USBTV Overture staging")

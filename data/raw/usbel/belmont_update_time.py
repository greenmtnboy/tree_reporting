#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Belmont's tree inventory: a fixed date.

Belmont's public TreeKeeper grid exposes no date column (no inventory date,
no last edit), so there is nothing to read a watermark from.  The inventory
was taken in 2022 and staff keep it current.

So this emits the date the inventory itself is through, and what re-reads
the portal is the city's weekly OSM extract: the staged extract's publication
time is in the published watermark, and a rebuild re-runs every partition.
An edit in Belmont therefore publishes within a week, not on the day.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness  # noqa: E402

INVENTORIED_THROUGH = datetime(2022, 12, 31, tzinfo=timezone.utc)


def fetch_modified_at() -> datetime:
    return INVENTORIED_THROUGH


if __name__ == "__main__":
    emit_freshness("USBEL", fetch_modified_at)

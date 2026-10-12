#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Somerville's TreeKeeper inventory.

TreeKeeper publishes no modified-at stamp, so the watermark is the newest
`Inventory Date` on any site: staff stamp it when they add or re-inspect a
tree.  A removal does not move it, so a deletion alone waits for the next
edit (or for another Boston source to change, which rebuilds the whole metro
parquet).
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
from shared.ingest import emit_freshness  # noqa: E402
from somerville_tree_info import newest_inventory_date  # noqa: E402


def fetch_modified_at() -> datetime:
    newest = newest_inventory_date()
    if newest is None:
        raise RuntimeError("Somerville TreeKeeper grid returned no sites")
    return datetime(newest.year, newest.month, newest.day, tzinfo=timezone.utc)


if __name__ == "__main__":
    emit_freshness("USBOS", fetch_modified_at)

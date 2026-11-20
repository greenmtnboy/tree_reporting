#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Syracuse's TreeKeeper inventory.

The ingest reads TreeKeeper's GeoServer WFS (`syracuseNY_WFS`), which
publishes no modified-at stamp, so the watermark is the newest
`InventoryDate` on any site: Forestry staff stamp it when they add or
re-inspect a site (2026-11-19 when the city was wired; the inventory is
edited most working days).  A removal alone does not move it, so a deletion
waits for the next edit -- Somerville's TreeKeeper probe makes the same trade.

`InventoryDate` is a bare date (`2026-11-19Z`), read as midnight UTC: a day's
granularity, so the zone cannot reorder two edits.

One feature, sorted descending on the column (`wfs_max_property`).  An outage
degrades through `get_json_with_retry`; an empty or unreadable column raises.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
from shared.ingest import emit_freshness  # noqa: E402
from shared.platforms.wfs import wfs_max_property  # noqa: E402
from syracuse_tree_info import LAYER  # noqa: E402


def fetch_modified_at() -> datetime:
    return wfs_max_property(LAYER, "InventoryDate")


if __name__ == "__main__":
    emit_freshness("USSYR", fetch_modified_at)

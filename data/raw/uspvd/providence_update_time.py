#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Providence's TreeKeeper inventory.

**TreeKeeper publishes no stamp for this project.**  Providence's public grid
exposes no date column at all -- no inventory date (Somerville's watermark),
no last edit, no work history (the `work` and `history` grids answer zero
rows) -- and the map has no layer metadata with a modified time.  Belmont's
grid is the same, and Belmont has no probe of its own because it rides on the
other Boston sources; a standalone city must have one.

So the watermark is **the start of the current week** (Monday 00:00 UTC), read
only after the grid has answered and still has the shape the ingest reads.
It is not a claim that the inventory changed: it bounds how stale the
published parquet can be to a week, at the cost of one re-read of ~36k sites a
week, which is the cadence the weekly OSM extract (re-stamped every run)
already rebuilds the city at.

The check is one selection and one grid row per layer the ingest reads: a
layer that selects no sites, or a row without the species or diameter column,
is the project changing shape and raises (it must not freeze the city
silently); a transport failure degrades to the epoch through `emit_freshness`.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
from shared.ingest import emit_freshness  # noqa: E402
from shared.platforms.treekeeper import fetch_grid, select_sites  # noqa: E402
from providence_tree_info import DBH_INCHES, SITES, SPECIES  # noqa: E402


def check_layers() -> None:
    for site in SITES:
        selection, count = select_sites(site)
        if count <= 0:
            raise RuntimeError(f"Providence TreeKeeper layer {site.map_layer} selects no sites")
        rows = fetch_grid(site, selection, limit=1).get("data") or []
        if not rows or any(col not in rows[0] for col in ("SITE_ID", SPECIES, DBH_INCHES)):
            raise RuntimeError(
                f"Providence TreeKeeper layer {site.map_layer}: grid row lacks "
                f"SITE_ID/{SPECIES}/{DBH_INCHES} ({sorted(rows[0]) if rows else 'no rows'})"
            )


def fetch_modified_at() -> datetime:
    check_layers()
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=today.weekday())


if __name__ == "__main__":
    emit_freshness("USPVD", fetch_modified_at)

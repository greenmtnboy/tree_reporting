#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Basel's garden inventory.

`ms:IG_InventarGartenanlagen` carries no date column, and neither the WFS
nor its ISO metadata record publishes a revision stamp for the layer, so
there is nothing on the source to watch.  The watermark is a constant, bumped
by hand: the day the layer was last reviewed -- the contract Copenhagen's
monuments have.  The inventory is a slow register (50 gardens, revised in
years, not days); bump `LAST_REVIEWED` after checking it and the weekly
landmark lane republishes.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness

LAST_REVIEWED = datetime(2026, 11, 16, tzinfo=timezone.utc)


def fetch_modified_at() -> datetime:
    return LAST_REVIEWED


if __name__ == "__main__":
    emit_freshness("CHBAS", fetch_modified_at, label="CHBAS landmarks")

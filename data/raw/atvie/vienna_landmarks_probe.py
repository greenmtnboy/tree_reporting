#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Vienna's landmarks.

Neither layer (`HISTGARTENOGD`, `SEHENSWUERDIGOGD`) carries a date column,
the WFS publishes no layer-level stamp, and the data.gv.at catalogue records
for both read 2025-11-20 -- the date the catalogue was migrated, not an edit.
So the watermark is a constant, bumped by hand: the day the layers were last
reviewed.  That is Copenhagen's contract for its monuments, and it is honest
about what would trigger a rebuild -- a person looking at the layers again.
Bump `LAST_REVIEWED` after checking them, and the weekly landmark lane
republishes.
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
    emit_freshness("ATVIE", fetch_modified_at, label="ATVIE landmarks")

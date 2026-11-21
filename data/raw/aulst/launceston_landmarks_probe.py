#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Launceston's Tasmanian Heritage Register places.

The register layer (`HT/HT_Public/MapServer/0` on theLIST) is a MapServer
layer: no `editingInfo`, no date column, and the data.gov.au records for the
register carry only a catalogue-harvest date.  So the watermark is a
constant, bumped by hand: the day the layer was last reviewed (Vienna's and
Copenhagen's contract).  Bump `LAST_REVIEWED` after checking the register,
and the weekly landmark lane republishes.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness

LAST_REVIEWED = datetime(2026, 11, 21, tzinfo=timezone.utc)


def fetch_modified_at() -> datetime:
    return LAST_REVIEWED


if __name__ == "__main__":
    emit_freshness("AULST", fetch_modified_at, label="AULST landmarks")

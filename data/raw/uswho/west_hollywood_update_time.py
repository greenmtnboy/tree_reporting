#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for West Hollywood's City Tree Inventory: its `rowsUpdatedAt`.

The dataset has not moved since December 2015; the probe is what lets a
republish land.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness
from shared.platforms.socrata import SocrataDataset, rows_updated_at

DATASET = SocrataDataset("data.weho.org", "qqwt-wx9z")


def fetch_modified_at() -> datetime:
    return rows_updated_at(DATASET)


if __name__ == "__main__":
    emit_freshness("USWHO", fetch_modified_at)

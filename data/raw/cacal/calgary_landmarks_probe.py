#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Calgary's historic resources register.

The dataset's own `rowsUpdatedAt`, so the weekly landmark lane only rebuilds
Calgary's parquet when the register (870 rows) has actually been republished.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness
from _socrata_shared import SocrataDataset, rows_updated_at

DATASET = SocrataDataset("data.calgary.ca", "99yf-6c5u")


def fetch_modified_at() -> datetime:
    return rows_updated_at(DATASET)


if __name__ == "__main__":
    emit_freshness("CACAL", fetch_modified_at, label="CACAL landmarks")

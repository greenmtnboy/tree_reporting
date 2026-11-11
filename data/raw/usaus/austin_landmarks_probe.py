#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Austin's historic landmark register.

`rowsUpdatedAt` of the "Historical Landmarks" dataset (`8k49-bptr`) on
data.austintexas.gov, so the weekly landmark lane only rebuilds Austin's
parquet when the Planning Department's feed has actually changed the rows.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness
from shared.platforms.socrata import SocrataDataset, rows_updated_at

DATASET = SocrataDataset("data.austintexas.gov", "8k49-bptr")


def fetch_modified_at() -> datetime:
    return rows_updated_at(DATASET)


if __name__ == "__main__":
    emit_freshness("USAUS", fetch_modified_at, label="USAUS landmarks")

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Los Angeles's municipal tree inventory.

Reads `rowsUpdatedAt` off the dataset's Socrata view metadata, so a refresh
only re-downloads the inventory when the city has actually republished it.

This was three byte-identical copies -- Los Angeles, and the other two Socrata
cities -- before `_socrata_shared` existed.  See `rows_updated_at` for why
it is that field rather than `viewLastModified`, and why it raises rather
than degrading when the field is missing.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness
from _socrata_shared import SocrataDataset, rows_updated_at

DATASET = SocrataDataset("data.lacity.org", "vt5t-mscf")


def fetch_modified_at() -> datetime:
    return rows_updated_at(DATASET)


if __name__ == "__main__":
    emit_freshness("USLAX", fetch_modified_at)

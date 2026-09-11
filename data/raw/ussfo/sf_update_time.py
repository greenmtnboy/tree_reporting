#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for San Francisco's municipal tree inventory.

Reads `rowsUpdatedAt` off the dataset's Socrata view metadata, so a refresh
only re-downloads the inventory when the city has actually republished it.

This was three byte-identical copies -- San Francisco, and the other two Socrata
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

# data.sf.gov, not the old data.sfgov.org: the latter 301s for metadata but
# answers the *row* query with a bare nginx 403, so the two halves of this
# city disagreed about which host works.  See `sf_tree_info.py` for the
# dataset migration that renamed every column under this same four-four.
DATASET = SocrataDataset("data.sf.gov", "tkzw-k3nq")


def fetch_modified_at() -> datetime:
    return rows_updated_at(DATASET)


if __name__ == "__main__":
    emit_freshness("USSFO", fetch_modified_at)

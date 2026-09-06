#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Edmonton's municipal tree inventory.

Reads `rowsUpdatedAt` off the dataset's view metadata -- one small JSON
response against the 481k-row Trees dataset -- so a refresh only re-downloads the
inventory when the city has actually republished it.

`rows_updated_at` raises rather than degrading when the field is absent: that
is the portal changing its metadata shape, not the portal being down, and
`emit_freshness` must not turn it into "no new data" (which would freeze
Edmonton's Parquet silently and for ever).  A genuine outage is caught by
`get_json_with_retry` inside it and does degrade to the epoch.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness
from _socrata_shared import SocrataDataset, rows_updated_at

DATASET = SocrataDataset("data.edmonton.ca", "eecg-fc54")


def fetch_modified_at() -> datetime:
    return rows_updated_at(DATASET)


if __name__ == "__main__":
    emit_freshness("CAEDM", fetch_modified_at)

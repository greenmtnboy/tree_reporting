#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Toronto's Places of Interest and Attractions.

The resource's own publication time, through `_ckan_shared.data_last_modified`,
so the weekly landmark lane only rebuilds Toronto's parquet when the 178-place
list has actually been republished.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ckan_shared import CkanResource, data_last_modified
from _ingest_shared import emit_freshness

RESOURCE = CkanResource(
    "ckan0.cf.opendata.inter.prod-toronto.ca",
    "f131dbb9-37b6-4e3e-9323-ff7f1c97395d",
)


def fetch_modified_at() -> datetime:
    return data_last_modified(RESOURCE)


if __name__ == "__main__":
    emit_freshness("CATOR", fetch_modified_at, label="CATOR landmarks")

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Longueuil's parks.

The resource's own publication time, through `_ckan_shared.data_last_modified`,
so the weekly landmark lane only rebuilds Longueuil's parquet when the 223-park
file has actually been republished.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ckan_shared import CkanResource, data_last_modified
from _ingest_shared import emit_freshness

RESOURCE = CkanResource(
    "www.donneesquebec.ca/recherche", "481ca9a8-7ab7-4937-9092-66d3f4acb6d3"
)


def fetch_modified_at() -> datetime:
    return data_last_modified(RESOURCE)


if __name__ == "__main__":
    emit_freshness("CALON", fetch_modified_at, label="CALON landmarks")

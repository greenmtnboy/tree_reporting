#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Milan's monumental-tree register (DS2489).

The GeoJSON resource's own publication time, through
`shared.platforms.ckan.data_last_modified`, so a new listing republishes the
city and nothing else does.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness
from shared.platforms.ckan import CkanResource, data_last_modified

RESOURCE = CkanResource(
    "dati.comune.milano.it", "ea95b273-0a6d-444a-a1fb-b83eae24ce4d"
)


def fetch_modified_at() -> datetime:
    return data_last_modified(RESOURCE)


if __name__ == "__main__":
    emit_freshness("ITMIL", fetch_modified_at, label="ITMIL monumental trees")

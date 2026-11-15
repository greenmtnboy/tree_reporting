#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Milan's parks (DS3018): the resource's publication time,
through `shared.platforms.ckan.data_last_modified`."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness
from shared.platforms.ckan import CkanResource, data_last_modified

RESOURCE = CkanResource(
    "dati.comune.milano.it", "66f1237d-e456-4432-9cdc-4f1384a15069"
)


def fetch_modified_at() -> datetime:
    return data_last_modified(RESOURCE)


if __name__ == "__main__":
    emit_freshness("ITMIL", fetch_modified_at, label="ITMIL landmarks")

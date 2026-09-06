#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Longueuil's tree inventory.

Two small JSON reads against a 25 MB GeoJSON export, so a refresh only
re-downloads the file when the city has actually republished it -- which it
does rarely: the resource last moved 2024-03-01.

The package's `metadata_modified` is 2026-02-09, nearly two years later, and
`data_last_modified` deliberately does not read it: that stamp moves for a
description edit, and following it would rebuild Longueuil for a typo. See the
note in `_ckan_shared`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ckan_shared import CkanResource, data_last_modified
from _ingest_shared import emit_freshness

RESOURCE = CkanResource(
    "www.donneesquebec.ca/recherche", "23cde69a-a1d7-4775-8271-e3b46b3a6d83"
)


def fetch_modified_at() -> datetime:
    return data_last_modified(RESOURCE)


if __name__ == "__main__":
    emit_freshness("CALON", fetch_modified_at)

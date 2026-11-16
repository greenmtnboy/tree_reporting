#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for the City of Port Phillip trees on data.gov.au.

`shared.platforms.ckan.data_last_modified`: the later of the resource's
`last_modified` and the package's `last_refreshed`.  The resource has not
moved since 2022-07-28; the probe is what lets a re-publish land.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.ckan import CkanResource, data_last_modified
from shared.ingest import emit_freshness

RESOURCE = CkanResource("data.gov.au/data", "dd8aa3f7-d5c2-459b-af5c-9b31a6848455")


def fetch_modified_at() -> datetime:
    return data_last_modified(RESOURCE)


if __name__ == "__main__":
    emit_freshness("AUMEL", fetch_modified_at)

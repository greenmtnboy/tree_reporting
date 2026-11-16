#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for the Hobsons Bay City Council street and park trees on data.gov.au.

`shared.platforms.ckan.data_last_modified`: the later of the resource's
`last_modified` and the package's `last_refreshed`.  The resource has not
moved since 2019-03-01; the probe is what lets a re-publish land.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.ckan import CkanResource, data_last_modified
from shared.ingest import emit_freshness

RESOURCE = CkanResource("data.gov.au/data", "339238b6-3c2c-4649-aa49-501ee12a6c84")


def fetch_modified_at() -> datetime:
    return data_last_modified(RESOURCE)


if __name__ == "__main__":
    emit_freshness("AUMEL", fetch_modified_at)

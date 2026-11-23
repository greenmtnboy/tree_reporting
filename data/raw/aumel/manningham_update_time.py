#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Manningham City Council's street trees on data.gov.au.

`shared.platforms.ckan.data_last_modified` on the CSV resource the ingest
reads (`manningham_tree_info.py`): the later of the resource's
`last_modified` and the package's `last_refreshed`.  The resource has stood
at 2019-11-10; the probe is what lets a re-publish land.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.ckan import CkanResource, data_last_modified
from shared.ingest import emit_freshness

RESOURCE = CkanResource("data.gov.au/data", "4797e8de-9750-4ed7-9d84-ea318302b881")


def fetch_modified_at() -> datetime:
    return data_last_modified(RESOURCE)


if __name__ == "__main__":
    emit_freshness("AUMEL", fetch_modified_at)

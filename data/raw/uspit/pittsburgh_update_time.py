#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Pittsburgh's tree inventory on the WPRDC.

`shared.platforms.ckan.data_last_modified`: the later of the datastore
resource's `last_modified` and the package's `last_refreshed`.  The resource
has not moved since 2022-12-19 (the package says the data has not updated
since 2020); the probe is what lets a re-publish land.  The package's
`metadata_modified` (2025-12-12) moves on description edits and is
deliberately not the watermark.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.ckan import CkanResource, data_last_modified
from shared.ingest import emit_freshness

RESOURCE = CkanResource("data.wprdc.org", "1515a93c-73e3-4425-9b35-1cd11b2196da")


def fetch_modified_at() -> datetime:
    return data_last_modified(RESOURCE)


if __name__ == "__main__":
    emit_freshness("USPIT", fetch_modified_at)

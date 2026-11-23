#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for the City of Burnside street trees on data.sa.gov.au.

`shared.platforms.ckan.data_last_modified`: the later of the resource's
`last_modified` and the package's `last_refreshed`.  The resource has not
moved since 2019-09-02; the probe is what lets a re-publish land.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.ckan import CkanResource, data_last_modified
from shared.ingest import emit_freshness

# Kept in step with burnside_tree_info.py's RESOURCE.
RESOURCE = CkanResource("data.sa.gov.au/data", "6d1912aa-4775-4f5e-b00d-18456ad872a5")


def fetch_modified_at() -> datetime:
    return data_last_modified(RESOURCE)


if __name__ == "__main__":
    emit_freshness("AUADL", fetch_modified_at)

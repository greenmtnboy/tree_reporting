#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Brimbank City Council's street and open-space trees on data.gov.au.

`shared.platforms.ckan.data_last_modified` on each of the two shapefile
resources the ingest reads (`brimbank_tree_info.py`), and the later of them:
a re-upload of either file is new data.  Both have stood at 2019-03-12; the
probe is what lets a re-publish land.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.ckan import CkanResource, data_last_modified
from shared.ingest import emit_freshness

RESOURCES = (
    CkanResource("data.gov.au/data", "0072de55-3e43-4d0e-9a5e-7a9449b828fc"),  # street trees
    CkanResource("data.gov.au/data", "0df2776c-4432-4dcc-9cfd-1ea3efad2cfc"),  # open-space trees
)


def fetch_modified_at() -> datetime:
    return max(data_last_modified(resource) for resource in RESOURCES)


if __name__ == "__main__":
    emit_freshness("AUMEL", fetch_modified_at)

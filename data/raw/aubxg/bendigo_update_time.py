#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Greater Bendigo's tree inventory.

The inventory is the shapefile resource of the data.gov.au dataset
`city-of-greater-bendigo-environment-trees`; a re-upload moves the resource's
`last_modified` (2024-11-29T00:00:00 on 2026-11-21, matching the file's
HTTP Last-Modified).  `data_last_modified` reads that and the package's
`last_refreshed` (absent on data.gov.au) and takes the later, the shared
CKAN rule; the package's `metadata_modified` is only a fallback, so a
description edit does not rebuild the city.

An outage degrades through `get_json_with_retry` (inside the CKAN module);
a payload with no timestamp at all raises, because that is data.gov.au
changing its metadata shape.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness  # noqa: E402
from shared.platforms.ckan import CkanResource, data_last_modified  # noqa: E402

RESOURCE = CkanResource("data.gov.au/data", "b3f01081-924c-41b7-989a-cf521ca136ea")


def fetch_modified_at() -> datetime:
    return data_last_modified(RESOURCE)


if __name__ == "__main__":
    emit_freshness("AUBXG", fetch_modified_at)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Belfast's tree inventory.

The watermark is the council CSV's own `Last-Modified` (2026-09-10 on
2026-11-20), read with a HEAD on the download URL the OpenDataNI resource
record names.  The CKAN stamps do not track the data: the resource's
`last_modified` is null, its `created` is 2015, and the package's
`metadata_modified` (2026-10-28) moves on any description edit.

A transport failure or a non-JSON catalogue answer degrades to the epoch
through `emit_freshness`; a file served without `Last-Modified` raises (the
host changed shape).
"""

import sys
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, head_with_retry
from shared.platforms.ckan import CkanResource, resource_download_url

# Kept in step with belfast_tree_info.py.
RESOURCE = CkanResource("admin.opendatani.gov.uk", "b501ba21-e26d-46d6-b854-26a95db78dd9")


def fetch_modified_at() -> datetime:
    url = resource_download_url(RESOURCE)
    response = head_with_retry(url, timeout=60)
    stamp = response.headers.get("Last-Modified")
    if not stamp:
        raise RuntimeError(f"no Last-Modified header on {url}")
    return parsedate_to_datetime(stamp)


if __name__ == "__main__":
    emit_freshness("GBBFS", fetch_modified_at)

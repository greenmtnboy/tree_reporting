#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Adelaide's street trees.

The watermark is the S3 object's own `Last-Modified`: one HEAD against the
CSV the ingest reads.  The council's FTP drop (AWS Transfer) writes the file;
data.sa.gov.au's `resource_show` then records a `last_modified` for the CKAN
resource, but that is the catalogue's note of the upload (2025-11-06 for an
object stamped 2025-11-10 when this was written), so it can lag or lead the
file itself.  The object is what gets downloaded, so it is what gets stamped.

A response with no `Last-Modified` raises rather than degrading: that is the
publisher changing how the file is served, not an outage, and
`emit_freshness` must not read it as "no new data".  An outage is retried by
`head_with_retry` and then degrades to the epoch.
"""

import sys
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, head_with_retry

# Kept in step with adelaide_tree_info.py's SOURCE_URL.
SOURCE_URL = (
    "https://s3.ap-southeast-2.amazonaws.com/dmzweb.adelaidecitycouncil.com/"
    "OpenData/Street_Trees/Street_Trees.csv"
)


def fetch_modified_at() -> datetime:
    response = head_with_retry(SOURCE_URL, timeout=60)
    stamp = response.headers.get("Last-Modified")
    if not stamp:
        raise RuntimeError(f"no Last-Modified header on {SOURCE_URL}")
    return parsedate_to_datetime(stamp)


if __name__ == "__main__":
    emit_freshness("AUADL", fetch_modified_at)

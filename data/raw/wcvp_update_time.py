#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for the WCVP release: the zip's own `Last-Modified`.

Kew replaces `wcvp.zip` in place when it cuts a release (v16 was 2026-06-04)
and publishes no catalogue stamp, so one HEAD request is the watermark.  A
missing header raises rather than degrading -- that is Kew changing how the
file is served, not an outage.
"""

from shared.ingest import emit_freshness, head_with_retry
from shared.wcvp import WCVP_ZIP_URL, zip_last_modified


def fetch_modified_at():
    return zip_last_modified(head_with_retry(WCVP_ZIP_URL, timeout=60).headers)


if __name__ == "__main__":
    emit_freshness(None, fetch_modified_at, label="wcvp")

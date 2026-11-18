#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Montpellier's tree inventory.

The watermark is the HTTP `Last-Modified` of the GeoJSON file the ingest
reads: one HEAD.  The portal's catalogue stamps are no use -- the CKAN
resource record still says 2022-08-29 although the file is rewritten every
morning, and the package's `metadata_modified` moves daily with the portal's
harvest whether or not anything changed.  The file's own stamp is what gets
downloaded, so it is what gets stamped; if the portal regenerates it daily the
city rebuilds daily, which is one 22 MB download.

A response with no `Last-Modified` raises rather than degrading: that is the
publisher changing how the file is served, not an outage.  A transport failure
is retried by `head_with_retry` and then degrades to the epoch through
`emit_freshness`, so Montpellier sits that run out.
"""

import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, head_with_retry

# Kept in step with montpellier_tree_info.py's SOURCE_URL.
SOURCE_URL = (
    "https://data.montpellier3m.fr/sites/default/files/ressources/MMM_MTP_Arbres.json"
)


def fetch_modified_at() -> datetime:
    response = head_with_retry(SOURCE_URL, timeout=60)
    stamp = response.headers.get("Last-Modified")
    if not stamp:
        raise RuntimeError(f"no Last-Modified header on {SOURCE_URL}")
    return parsedate_to_datetime(stamp).astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("FRMPL", fetch_modified_at)

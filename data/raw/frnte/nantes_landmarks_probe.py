#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Nantes's protected monuments.

The register is a file in the Ministry of Culture's POP bucket (the national
Monuments Historiques export, see `nantes_landmarks.py`), so the watermark is
its `Last-Modified`: one HEAD against a 100 MB CSV.  The file is national, so
the watermark moves when any department's designations change, not only
Nantes's; the rebuild that follows is one download.

A missing `Last-Modified` raises rather than degrading: the bucket always sends
one, so its absence means this URL is not the object we think it is.  A
transport failure raises `UpstreamUnavailable`, which `emit_freshness` turns
into the epoch so Nantes sits the tick out.
"""

import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, head_with_retry

# Kept in step with `nantes_landmarks.py`.
REGISTER_URL = "https://ministere-culture.s3.sbg.io.cloud.ovh.net/POP/merimee.csv"


def fetch_last_modified() -> datetime:
    response = head_with_retry(REGISTER_URL, timeout=60)
    header = response.headers.get("Last-Modified")
    if not header:
        raise RuntimeError(f"no Last-Modified header on {REGISTER_URL}")
    return parsedate_to_datetime(header).astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("FRNTE", fetch_last_modified)

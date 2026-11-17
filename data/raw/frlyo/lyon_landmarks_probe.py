#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for the Métropole de Lyon's protected monuments.

The register is a file in the Ministry of Culture's POP bucket (the national
Mérimée export Paris also reads), so the watermark is its `Last-Modified`: one
HEAD against a 100 MB CSV.  A missing header raises -- the bucket always sends
one, so its absence means this URL is not the object we think it is -- and a
transport failure degrades to the epoch through `emit_freshness`.
"""

import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, head_with_retry

# Kept in step with `lyon_landmarks.py`.
REGISTER_URL = "https://ministere-culture.s3.sbg.io.cloud.ovh.net/POP/merimee.csv"


def fetch_last_modified() -> datetime:
    response = head_with_retry(REGISTER_URL, timeout=60)
    header = response.headers.get("Last-Modified")
    if not header:
        raise RuntimeError(f"no Last-Modified header on {REGISTER_URL}")
    return parsedate_to_datetime(header).astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("FRLYO", fetch_last_modified)

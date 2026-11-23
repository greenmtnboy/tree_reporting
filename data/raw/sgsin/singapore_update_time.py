#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Singapore's Heritage Trees register.

data.gov.sg's v2 metadata endpoint reports the dataset's `lastUpdatedAt`
(`2026-10-29T10:06:51+08:00` on 2026-11-22), an ISO time with its offset, so
it is read as written and converted to UTC.  The features' own `FMEL_UPD_D`
stamps (`20250818152419`) lag it -- they mark the agency's last edit to each
row, not the publication -- and would need the whole file fetched.

An outage degrades through `get_json_with_retry`; a payload without
`lastUpdatedAt` raises, because that is data.gov.sg changing its metadata
shape.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry  # noqa: E402

DATASET_ID = "d_644ff187b6d14d6316f47284a4a6c81f"
METADATA_URL = f"https://api-production.data.gov.sg/v2/public/api/datasets/{DATASET_ID}/metadata"


def fetch_modified_at() -> datetime:
    payload = get_json_with_retry(METADATA_URL)
    data = payload.get("data") if isinstance(payload, dict) else None
    stamp = data.get("lastUpdatedAt") if isinstance(data, dict) else None
    if not stamp:
        raise RuntimeError(f"Singapore heritage trees: no data.lastUpdatedAt in {METADATA_URL}")
    parsed = datetime.fromisoformat(stamp)
    if parsed.tzinfo is None:
        raise RuntimeError(f"Singapore heritage trees: lastUpdatedAt {stamp!r} has no offset")
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("SGSIN", fetch_modified_at)

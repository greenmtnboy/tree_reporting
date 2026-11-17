#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Hamburg's heritage register (Denkmalkartierung).

The same watermark as the tree probe (`hamburg_update_time.py`): the WFS has
no edit date, so the clock is transparenz.hamburg.de's current version of the
register's metadata record (3B43E143-2C8B-43E8-8004-EE9EDA3EA563,
"Denkmalkartierung Baudenkmale Hamburg").  The catalogue mints a new package
per published version and suffixes the superseded ones' original id
(`###V<n>`), so the bare id matches exactly the current version, whose
`metadata_modified` (UTC) moves when the register is republished.

A count other than one, or a missing timestamp, raises (the catalogue changed
shape); a transport failure degrades to the epoch through `emit_freshness`.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

METADATA_ID = "3B43E143-2C8B-43E8-8004-EE9EDA3EA563"
SEARCH_URL = "https://suche.transparenz.hamburg.de/api/3/action/package_search"


def fetch_modified_at() -> datetime:
    payload = get_json_with_retry(
        SEARCH_URL,
        params={
            "fq": f'extras_metadata_original_id:"{METADATA_ID}"',
            "rows": "2",
            "fl": "name,metadata_modified",
        },
    )
    result = payload.get("result") or {}
    packages = result.get("results") or []
    if result.get("count") != 1 or len(packages) != 1:
        raise RuntimeError(
            f"Hamburg catalogue: expected one current version of {METADATA_ID}, "
            f"got count={result.get('count')!r}"
        )
    raw = packages[0].get("metadata_modified")
    if not raw:
        raise RuntimeError(f"Hamburg catalogue: {packages[0].get('name')} has no metadata_modified")
    stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("DEHAM", fetch_modified_at, label="DEHAM landmarks")

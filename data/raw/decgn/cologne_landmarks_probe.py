#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Cologne's landmarks (the Denkmalliste).

The Denkmalliste WFS publishes no layer stamp and no edit-date column (its
`geschützt_seit` is when a monument was designated, not when the list was
edited), so this reads the catalogue: the offenedaten-koeln.de (DKAN)
metastore record for "Liste der Kölner Denkmäler", the latest of the
dataset's and its distributions' `dctModified` -- 2025-11-27 when this was
written.  It moves when the city republishes the record; a designation added
without touching the record waits for that, which for a register amended a
few times a year is the right cadence.

A missing or unreadable `dctModified` raises -- the catalogue changing
shape -- and a transport failure degrades to the epoch through
`emit_freshness`.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

DATASET_ID = "1d8e7e99-42f3-4650-ac7d-a70012d3cff0"
METADATA_URL = (
    f"https://offenedaten-koeln.de/api/1/metastore/schemas/dataset/items/{DATASET_ID}"
)


def _parse(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def fetch_modified_at() -> datetime:
    payload = get_json_with_retry(METADATA_URL, timeout=60)
    if not isinstance(payload, dict) or payload.get("identifier") != DATASET_ID:
        raise RuntimeError(f"{METADATA_URL}: not the dataset record ({str(payload)[:200]!r})")
    raw = [payload.get("dctModified")]
    for distribution in payload.get("distribution") or []:
        if isinstance(distribution, dict):
            raw.append(distribution.get("dctModified"))
    stamps = [s for s in (_parse(v) for v in raw) if s is not None]
    if not stamps:
        raise RuntimeError(f"{METADATA_URL}: cannot read `dctModified` ({raw!r})")
    return max(stamps)


if __name__ == "__main__":
    emit_freshness("DECGN", fetch_modified_at, label="DECGN landmarks")

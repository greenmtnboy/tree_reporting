#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Cologne's tree register.

The WFS (`ms:baumkataster`, MapServer) carries no date column and publishes
no layer stamp -- its `timeStamp` is the time of the request -- so this falls
back to the catalogue: the offenedaten-koeln.de (DKAN) metastore record for
the dataset, whose `dctModified` (the dataset) and the distributions'
`dctModified` (the WFS and WMS entries) are read and the latest emitted.

What that costs: it is the catalogue's stamp, not the data's.  The catalogue
says the register is refreshed daily, but the stamp read 2025-11-27 on
2026-11-16 while the layer carried 242 trees planted in 2026, so it does
**not** move when the city refreshes the layer.  The city still rebuilds
weekly: `decgn_published_data_updated_through` is the greatest of this, the
community probe and the OSM staging probe, and the weekly OSM extract
re-stamps its staging object every run -- Vienna's arrangement.

A missing or unreadable `dctModified` raises -- the catalogue changing
shape -- and a transport failure degrades to the epoch through
`emit_freshness`.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

DATASET_ID = "65ed7e99-f144-410e-ae1f-3040f151c8c8"
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
    emit_freshness("DECGN", fetch_modified_at)

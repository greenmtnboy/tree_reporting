#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Vienna's tree register.

The WFS layer (`ogdwien:BAUMKATOGD`) carries no date column and GeoServer
publishes no layer-level stamp, so neither `wfs_max_property` nor an
`editingInfo` has anything to read.  This falls back to the catalogue: the
data.gv.at hub record for the dataset, whose `modified` (the dataset) and
`catalog_record.modified` (the record) are read and the later one emitted.

What that costs: it is the catalogue's stamp, not the data's.  It read
2025-11-20 / 2025-12-17 on 2026-11-16 while the layer carried 1,961 trees
planted in 2026 and its `OBJECTID`s showed a recent wholesale reload, so the
stamp does **not** move when the city reloads the layer.  The city still
rebuilds weekly: `atvie_published_data_updated_through` is the greatest of
this, the community probe and the OSM staging probe, and the weekly OSM
extract re-stamps its staging object every run.  A probe that tracks the
layer itself (a row count, `MAX(OBJECTID)`) is not a time, which is what
`freshness by` compares.

A missing or unreadable `modified` raises -- the catalogue changing shape --
and a transport failure degrades to the epoch through `emit_freshness`.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

DATASET_ID = "c91a4635-8b7d-43fe-9b27-d95dec8392a7"
METADATA_URL = f"https://www.data.gv.at/api/hub/search/datasets/{DATASET_ID}"


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
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        raise RuntimeError(f"{METADATA_URL}: no `result` object in the hub response")
    record = result.get("catalog_record") or {}
    stamps = [
        s
        for s in (_parse(result.get("modified")), _parse(record.get("modified")))
        if s is not None
    ]
    if not stamps:
        raise RuntimeError(
            f"{METADATA_URL}: cannot read `modified` "
            f"({result.get('modified')!r}, {record.get('modified')!r})"
        )
    return max(stamps)


if __name__ == "__main__":
    emit_freshness("ATVIE", fetch_modified_at)

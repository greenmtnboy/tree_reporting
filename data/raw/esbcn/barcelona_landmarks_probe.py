#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Barcelona's cultural points of interest.

The datastore is reloaded in place, so the resource's `last_modified` has
read 2021-06-11 for years.  Every row carries its own `modified` stamp from
the tourism database, so the watermark is the latest of those: one request,
`distinct=true` over that column sorted descending (Postgres puts any nulls
first, so a few values are asked for and the largest not in the future is
taken).  A missing column raises; an outage degrades through
`emit_freshness`.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry
from shared.platforms.ckan import CkanResource

RESOURCE = CkanResource(
    "opendata-ajuntament.barcelona.cat/data", "31431b23-d5b9-42b8-bcd0-a84da9d8c7fa"
)
STAMP_FIELD = "modified"


def _parse(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def fetch_modified_at() -> datetime:
    payload = get_json_with_retry(
        f"{RESOURCE.api}/datastore_search",
        params={
            "resource_id": RESOURCE.resource_id,
            "fields": STAMP_FIELD,
            "distinct": "true",
            "sort": f"{STAMP_FIELD} desc",
            "limit": "10",
        },
        timeout=RESOURCE.timeout,
    )
    result = payload.get("result") or {}
    names = {field.get("id") for field in result.get("fields") or []}
    if STAMP_FIELD not in names:
        raise RuntimeError(
            f"Barcelona cultural points datastore no longer has a "
            f"{STAMP_FIELD} column (fields: {sorted(n for n in names if n)})"
        )
    now = datetime.now(timezone.utc)
    stamps = [_parse(rec.get(STAMP_FIELD)) for rec in result.get("records") or []]
    stamps = [s for s in stamps if s is not None and s <= now]
    if not stamps:
        raise RuntimeError(
            f"Barcelona cultural points: no {STAMP_FIELD} value at or before "
            "now among the latest ten"
        )
    return max(stamps)


if __name__ == "__main__":
    emit_freshness("ESBCN", fetch_modified_at, label="ESBCN landmarks")

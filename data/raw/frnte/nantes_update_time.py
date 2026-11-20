#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Nantes's tree inventory.

Reads `metas.default.data_processed` from the OpenDataSoft catalogue record of
`244400404_patrimoine-arbore-nantes-metropole` on data.nantesmetropole.fr --
one small JSON request.  Not `modified`: the record pins that at
2013-06-20 and declares `modified_updates_on_data_change: false`, so it never
moves.  `data_processed` is when the portal last processed an upload of the
data (2026-02-20 when this was written, for an annual publication), written
with an explicit `+00:00` offset.

A transport failure or a maintenance page degrades to the epoch through
`get_json_with_retry` / `emit_freshness`, so Nantes sits out that run.  A
record without the field raises: the portal changed shape, and degrading
would freeze the city silently.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

METADATA_URL = (
    "https://data.nantesmetropole.fr/api/explore/v2.1/catalog/datasets/"
    "244400404_patrimoine-arbore-nantes-metropole"
)


def fetch_modified_at() -> datetime:
    meta = get_json_with_retry(METADATA_URL)
    stamp = ((meta.get("metas") or {}).get("default") or {}).get("data_processed")
    if not stamp:
        raise RuntimeError(
            "data.nantesmetropole.fr patrimoine-arbore: metas.default.data_processed is missing"
        )
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("FRNTE", fetch_modified_at)

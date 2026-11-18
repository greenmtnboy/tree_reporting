#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Strasbourg's tree inventory.

Reads `metas.default.modified` from the OpenDataSoft catalogue record of
`patrimoine_arbore` on data.strasbourg.eu -- one small JSON request.  The
record declares `modified_updates_on_data_change: true`, so the stamp moves
when the data does (the publisher refreshes monthly).  ODS writes it with an
explicit `+00:00` offset.

A transport failure or a maintenance page degrades to the epoch through
`get_json_with_retry` / `emit_freshness`, so Strasbourg sits out that run.  A
record without the field raises: the portal changed shape, and degrading
would freeze the city silently.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

METADATA_URL = (
    "https://data.strasbourg.eu/api/explore/v2.1/catalog/datasets/patrimoine_arbore"
)


def fetch_modified_at() -> datetime:
    meta = get_json_with_retry(METADATA_URL)
    stamp = ((meta.get("metas") or {}).get("default") or {}).get("modified")
    if not stamp:
        raise RuntimeError(
            "data.strasbourg.eu patrimoine_arbore: metas.default.modified is missing"
        )
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("FRSXB", fetch_modified_at)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Bordeaux's tree inventory.

Reads `metas.default.modified` from the OpenDataSoft catalogue record of
`ec_arbre_p` on opendata.bordeaux-metropole.fr -- one small JSON request.  The
record declares `modified_updates_on_data_change: true` and
`modified_updates_on_metadata_change: false`, so the stamp moves when the
data does (the publisher reprocesses daily) and not when a description is
edited.  ODS writes it with an explicit `+00:00` offset.

A transport failure or a maintenance page degrades to the epoch through
`get_json_with_retry` / `emit_freshness`, so Bordeaux sits out that run.  A
record without the field raises: the portal changed shape, and degrading
would freeze the city silently.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

METADATA_URL = (
    "https://opendata.bordeaux-metropole.fr/api/explore/v2.1/catalog/datasets/ec_arbre_p"
)


def fetch_modified_at() -> datetime:
    meta = get_json_with_retry(METADATA_URL)
    stamp = ((meta.get("metas") or {}).get("default") or {}).get("modified")
    if not stamp:
        raise RuntimeError(
            "opendata.bordeaux-metropole.fr ec_arbre_p: metas.default.modified is missing"
        )
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("FRBOD", fetch_modified_at)

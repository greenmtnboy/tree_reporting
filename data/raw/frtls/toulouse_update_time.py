#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Toulouse's tree inventory.

Reads `metas.default.modified` from the OpenDataSoft catalogue record of
`arbres-urbains` on data.toulouse-metropole.fr -- one small JSON request.  The
record declares `modified_updates_on_data_change: true` and
`modified_updates_on_metadata_change: false`, so the stamp moves when the
data does (the publisher refreshes monthly) and not when a description is
edited.  ODS writes it with an explicit `+00:00` offset.

A transport failure or a maintenance page degrades to the epoch through
`get_json_with_retry` / `emit_freshness`, so Toulouse sits out that run.  A
record without the field raises: the portal changed shape, and degrading
would freeze the city silently.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

METADATA_URL = (
    "https://data.toulouse-metropole.fr/api/explore/v2.1/catalog/datasets/arbres-urbains"
)


def fetch_modified_at() -> datetime:
    meta = get_json_with_retry(METADATA_URL)
    stamp = ((meta.get("metas") or {}).get("default") or {}).get("modified")
    if not stamp:
        raise RuntimeError(
            "data.toulouse-metropole.fr arbres-urbains: metas.default.modified is missing"
        )
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("FRTLS", fetch_modified_at)

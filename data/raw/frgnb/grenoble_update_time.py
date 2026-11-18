#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Grenoble's tree inventory.

The ingest (`grenoble_tree_info.py`) reads two datasets' CSV resources on
data.metropolegrenoble.fr.  The dataset records' own `metadata_modified` moves
every day whether or not anything was uploaded (the portal's harvester touches
it), so it would rebuild the city daily.  The watermark is instead the newest
`creation_date` of each dataset's CSV resource, from
`/d4c/api/datasets/1.0/{id}/` -> `resources_versions[].versions[]`: a
re-upload creates a new version.  The portal writes it without an offset; it
is UTC (the city CSV's 2026-02-28 23:35:05 matches the file's
`Last-Modified: Sat, 28 Feb 2026 23:35:03 GMT`).

A transport failure or a holding page degrades to the epoch through
`get_json_with_retry` / `emit_freshness`.  A record without a CSV version or
with an unreadable date raises: the portal changed shape.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

PORTAL = "https://data.metropolegrenoble.fr"
# Kept in step with `grenoble_tree_info.py`.
DATASETS = ("arbres-grenoble", "patrimoine_arbore_du_territoire_metropolitain")


def csv_uploaded_at(dataset: str) -> datetime:
    meta = get_json_with_retry(f"{PORTAL}/d4c/api/datasets/1.0/{dataset}/", timeout=60)
    stamps = [
        version.get("creation_date")
        for resource in meta.get("resources_versions") or []
        if str(resource.get("name", "")).lower().endswith(".csv")
        for version in resource.get("versions") or []
    ]
    stamps = [s for s in stamps if s]
    if not stamps:
        raise RuntimeError(f"{dataset}: no CSV resource version in the portal's dataset record")
    return max(
        datetime.fromisoformat(s).replace(tzinfo=timezone.utc) for s in stamps
    )


def fetch_modified_at() -> datetime:
    return max(csv_uploaded_at(dataset) for dataset in DATASETS)


if __name__ == "__main__":
    emit_freshness("FRGNB", fetch_modified_at)

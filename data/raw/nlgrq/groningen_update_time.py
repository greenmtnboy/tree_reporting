#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Groningen's tree inventory.

The GeoServer layer has no edit-date column and data.groningen.nl (Drupal)
exposes no metadata API, so the probe reads the national catalogue that
harvests it: `metadata_modified` of the CKAN package
`groningen-bomen-gemeente-groningen` on data.overheid.nl, one small JSON
request.  It moves when the city republishes the record -- 2026-11-02, the
"Laatste wijziging" the city's own dataset page shows for its monthly
refresh -- and not on every harvest (other Groningen records there still
carry 2025 stamps).  CKAN writes it as naive UTC.

A transport failure or a maintenance page degrades to the epoch through
`get_json_with_retry` / `emit_freshness`, so Groningen sits out that run.  A
record without the field raises: the catalogue changed shape, and degrading
would freeze the city silently.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

METADATA_URL = "https://data.overheid.nl/data/api/3/action/package_show"
PACKAGE = "groningen-bomen-gemeente-groningen"


def fetch_modified_at() -> datetime:
    payload = get_json_with_retry(METADATA_URL, params={"id": PACKAGE}, timeout=60)
    stamp = (payload.get("result") or {}).get("metadata_modified")
    if not stamp:
        raise RuntimeError(f"data.overheid.nl {PACKAGE}: metadata_modified is missing")
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


if __name__ == "__main__":
    emit_freshness("NLGRQ", fetch_modified_at)

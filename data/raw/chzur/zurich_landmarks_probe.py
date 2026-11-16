#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Zürich's heritage inventory (Denkmalpflege-Inventar).

The same watermark as the tree probe (`zurich_update_time.py`): the
catalogue's `dateLastUpdated` for the `geo_denkmalpflege_inventar` package on
data.stadt-zuerich.ch, a `DD.MM.YYYY` date the Geoportal stamps when it
republishes the layer (weekly; `15.11.2026` when this was written).  Not
`metadata_modified`, which the harvester moves every morning regardless, and
the WFS layer has no date column.

A missing or unparseable date raises (the catalogue changed shape); a
transport failure degrades to the epoch through `emit_freshness`.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

METADATA_URL = "https://data.stadt-zuerich.ch/api/3/action/package_show"
PACKAGE_ID = "geo_denkmalpflege_inventar"


def fetch_modified_at() -> datetime:
    payload = get_json_with_retry(METADATA_URL, params={"id": PACKAGE_ID}, timeout=60)
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"{PACKAGE_ID}: package_show returned no result")
    value = result.get("dateLastUpdated")
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{PACKAGE_ID}: dateLastUpdated is missing ({value!r})")
    try:
        day = datetime.strptime(value.strip(), "%d.%m.%Y")
    except ValueError as exc:
        raise RuntimeError(f"{PACKAGE_ID}: cannot read dateLastUpdated={value!r}") from exc
    return day.replace(tzinfo=timezone.utc)


if __name__ == "__main__":
    emit_freshness("CHZUR", fetch_modified_at, label="CHZUR landmarks")

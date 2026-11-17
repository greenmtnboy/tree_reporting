#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Hamburg's street-tree cadastre.

The WFS (`HH_WFS_Strassenbaumkataster`) carries no edit date -- no timestamp
column, and `daten_aus_vorjahr` is only the data year -- so the watermark is
the city's open-data catalogue, transparenz.hamburg.de (CKAN).  The catalogue
harvests the city's metadata record C1C61928-C602-4E37-AF31-2D23901E2540 and
mints a **new package for each published version** (`strassenbaumkataster-
hamburg33`, `...34`, ...), renaming the superseded ones' original id with a
`###V<n>` suffix.  So a search on the bare original id returns exactly the
current version, and its `metadata_modified` (UTC) moves when the cadastre is
republished -- three or four times a year.

A count other than one, or a missing timestamp, is the catalogue changing
shape and raises; a non-JSON answer or timeout is an outage and degrades
(`emit_freshness`).
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

METADATA_ID = "C1C61928-C602-4E37-AF31-2D23901E2540"
SEARCH_URL = "https://suche.transparenz.hamburg.de/api/3/action/package_search"


def fetch_modified_at() -> datetime:
    payload = get_json_with_retry(
        SEARCH_URL,
        params={
            "fq": f'extras_metadata_original_id:"{METADATA_ID}"',
            "rows": "2",
            "fl": "name,metadata_modified",
        },
    )
    result = payload.get("result") or {}
    packages = result.get("results") or []
    if result.get("count") != 1 or len(packages) != 1:
        raise RuntimeError(
            f"Hamburg catalogue: expected one current version of {METADATA_ID}, "
            f"got count={result.get('count')!r}"
        )
    raw = packages[0].get("metadata_modified")
    if not raw:
        raise RuntimeError(f"Hamburg catalogue: {packages[0].get('name')} has no metadata_modified")
    stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("DEHAM", fetch_modified_at)

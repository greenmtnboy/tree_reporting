#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Edinburgh's landmarks (Historic Environment Scotland).

HES's designations MapServer publishes no `editingInfo` and refuses
`outStatistics` (the layers are file-backed), so this reads the date columns
of Edinburgh's entries -- `DESIGNATED`, `AMENDED` and `UPDATED` (the record's
own edit date) on the listed buildings, scheduled monuments and gardens and
designed landscapes with `LOCAL_AUTH = 'Edinburgh'` -- and takes the newest.
About 10,200 attribute-only rows in FID ranges, the same reads the landmark
script makes without geometry.  A delisting removes a row without moving a
date and is picked up at the next change.

Requests go through `get_json_with_retry`, so an outage degrades to the
epoch; no dates at all raises, since that means the fields were renamed.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import esri_ms_to_datetime  # noqa: E402
from shared.ingest import emit_freshness  # noqa: E402
from edinburgh_landmarks import GARDENS, LISTED, SCHEDULED, fetch_all  # noqa: E402

DATE_FIELDS = ("DESIGNATED", "AMENDED", "UPDATED")


def fetch_modified_at() -> datetime:
    stamps = [
        esri_ms_to_datetime((feature.get("attributes") or {}).get(field))
        for layer in (LISTED, SCHEDULED, GARDENS)
        for feature in fetch_all(layer, ",".join(DATE_FIELDS), geometry=False)
        for field in DATE_FIELDS
    ]
    stamps = [s for s in stamps if s is not None]
    if not stamps:
        raise RuntimeError("no HES designation or update date for Edinburgh")
    return max(stamps)


if __name__ == "__main__":
    emit_freshness("GBEDI", fetch_modified_at, label="GBEDI landmarks")

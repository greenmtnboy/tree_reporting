#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Welland's designated heritage properties.

The `OPENDATA_Heritage_Parcels` layer (a MapServer layer on the City's own
ArcGIS Server) publishes no `editingInfo` and no edit-date column, and its
only date-like field, `Year_Desig`, is an integer year.  The Hub catalogue
feed does not list the layer, so `hub_last_modified` has nothing to read.
What moves is the ArcGIS item's `modified` (2026-10-07 when the city was
wired: the City's open data sync re-stamps its items), which is the
watermark.  An outage degrades through `get_json_with_retry`; an item with no
`modified` raises, because that is a schema change.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import esri_ms_to_datetime
from shared.ingest import emit_freshness, get_json_with_retry

ITEM = "0aa78d20dd6a4dac95e2e8e5bca0e8c2"
ITEM_URL = f"https://www.arcgis.com/sharing/rest/content/items/{ITEM}"


def fetch_modified_at() -> datetime:
    payload = get_json_with_retry(ITEM_URL, params={"f": "json"}, timeout=60)
    stamp = esri_ms_to_datetime(payload.get("modified"))
    if stamp is None:
        raise RuntimeError(f"no `modified` on ArcGIS item {ITEM}")
    return stamp


if __name__ == "__main__":
    emit_freshness("CAWLD", fetch_modified_at, label="CAWLD landmarks")

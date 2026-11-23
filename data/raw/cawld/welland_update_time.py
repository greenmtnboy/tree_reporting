#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Welland's tree inventory.

The layer is a MapServer layer on the City's own ArcGIS Server
(`OpenData/OPENDATA_Active_Trees/MapServer/0`) and publishes no
`editingInfo` and no edit-date column.  What it carries is `DatePlant`, the
planting date the forestry team stamps on a new tree (2021-03-03 the newest
when the city was wired), which moves when a planting is recorded; a
correction to an existing tree moves nothing inside the layer, so the ArcGIS
item's `modified` (2023-06-27 then) is the backstop, and the later of the
two wins -- Madison's rule.  (The Hub's DCAT `modified`, 2022-06-07, is older
than the item's and adds nothing.)

An outage degrades through `get_json_with_retry`; a missing field, an empty
statistics row or an item with no `modified` raises, because that is a schema
change.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_datetime, field_max
from shared.ingest import emit_freshness, get_json_with_retry

LAYER = FeatureLayer(
    "https://arcgisweb.welland.ca/serverprodpub/rest/services/OpenData/"
    "OPENDATA_Active_Trees/MapServer/0",
    timeout=60,
)
ITEM = "afcb6b5308de40b1830a3ebb2369a83b"
ITEM_URL = f"https://www.arcgis.com/sharing/rest/content/items/{ITEM}"


def item_modified() -> datetime:
    payload = get_json_with_retry(ITEM_URL, params={"f": "json"}, timeout=60)
    stamp = esri_ms_to_datetime(payload.get("modified"))
    if stamp is None:
        raise RuntimeError(f"no `modified` on ArcGIS item {ITEM}")
    return stamp


def fetch_modified_at() -> datetime:
    return max(field_max(LAYER, "DatePlant"), item_modified())


if __name__ == "__main__":
    emit_freshness("CAWLD", fetch_modified_at)

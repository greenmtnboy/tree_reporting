#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Santa Monica's tree inventory.

The ingest reads `Trees/MapServer/0` on gis.santamonica.gov, which publishes
no `editingInfo` and no date column of any kind (its FeatureServer twin is
query-only and has neither), so neither `layer_last_edit` nor `field_max` has
anything to read.  This falls back to the catalogue: the ArcGIS Online items
that register the service -- the Map Service item the ingest names
(64f5db2cbb224f6d87387999a300b962, `modified` 2026-09-15) and the Feature
Service item for the same table (3712049fddf1459eb880731a831af8a6,
2026-09-17) -- and emits the later `modified`.

What that costs: it is the catalogue's stamp, not the data's, and moves when
the city republishes or re-describes the service rather than on every tree
edit.  The city still rebuilds at least weekly: the published watermark is
the greatest of this, the community probe and the OSM staging probe, and the
weekly OSM extract re-stamps its staging object every run.  A probe that
tracks the layer itself (a row count, `MAX(inventoryid)`) is not a time,
which is what `freshness by` compares.

A missing or unreadable `modified` raises -- the catalogue changing shape --
and a transport failure degrades to the epoch through `emit_freshness`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import esri_ms_to_datetime
from shared.ingest import emit_freshness, get_json_with_retry

ITEMS = (
    "64f5db2cbb224f6d87387999a300b962",  # Trees (Map Service), the ingest's layer
    "3712049fddf1459eb880731a831af8a6",  # Trees (Feature Service), same table
)
ITEM_URL = "https://www.arcgis.com/sharing/rest/content/items/{}"


def item_modified(item: str) -> datetime:
    payload = get_json_with_retry(ITEM_URL.format(item), params={"f": "json"}, timeout=60)
    stamp = esri_ms_to_datetime(payload.get("modified"))
    if stamp is None:
        raise RuntimeError(f"no `modified` on ArcGIS item {item}")
    return stamp


def fetch_modified_at() -> datetime:
    return max(item_modified(item) for item in ITEMS)


if __name__ == "__main__":
    emit_freshness("USSMO", fetch_modified_at)

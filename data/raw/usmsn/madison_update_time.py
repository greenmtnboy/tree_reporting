#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Madison's street tree inventory.

The ingest reads `Public/OPEN_DATA/MapServer/0`, a MapServer layer with no
`editingInfo` and no edit-tracking columns.  What it does carry is the
forester's own dates: `INV_DATE` (last inventory) and `INSPECT_DT` (last
inspection), which Urban Forestry stamps when it visits a site -- planting,
removing or re-measuring a tree is a visit.  The layer reads the live
database (2026-06-02 was the newest inspection when the city was wired,
though the item's description says "Data last updated 11/19/2025"), so the
probe is the latest of the two maxima and, as a backstop for an edit that
moves neither, the ArcGIS item's `modified` (2025-12-03 then).

An outage degrades through `get_json_with_retry`; a layer that no longer has
the columns, or answers MAX with nothing, raises (`field_max`).
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_datetime, field_max
from shared.ingest import emit_freshness, get_json_with_retry

LAYER = FeatureLayer(
    "https://maps.cityofmadison.com/arcgis/rest/services/Public/OPEN_DATA/MapServer/0",
    timeout=60,
)
ITEM = "b700541a20e446839b18d62426c266a3"
ITEM_URL = f"https://www.arcgis.com/sharing/rest/content/items/{ITEM}"


def item_modified() -> datetime:
    payload = get_json_with_retry(ITEM_URL, params={"f": "json"}, timeout=60)
    stamp = esri_ms_to_datetime(payload.get("modified"))
    if stamp is None:
        raise RuntimeError(f"no `modified` on ArcGIS item {ITEM}")
    return stamp


def fetch_modified_at() -> datetime:
    return max(
        field_max(LAYER, "INSPECT_DT"),
        field_max(LAYER, "INV_DATE"),
        item_modified(),
    )


if __name__ == "__main__":
    emit_freshness("USMSN", fetch_modified_at)

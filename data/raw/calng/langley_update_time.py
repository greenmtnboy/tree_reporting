#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Langley Township's tree inventory.

The `Tree_Inventory` layer is a hosted ArcGIS Online feature layer that the
Township's FME account reloads whole; its `editingInfo.dataLastEditDate`
moves with every reload (2026-11-20 when the city was wired).  The stamp is
epoch milliseconds, a real UTC instant.  A reload that changes nothing still
moves it, which costs one rebuild per reload, not a stale city.

An outage degrades through `get_json_with_retry`; a layer that stops
publishing `editingInfo` raises, because that is a schema change.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services5.arcgis.com/frpHL0Fv8koQRVWY/arcgis/rest/services/"
    "Tree_Inventory/FeatureServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("CALNG", fetch_modified_at)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Latrobe City's tree inventory.

The `Tree_Inventory_All View` layer is a hosted ArcGIS Online view with
editing tracked, so the watermark is its own `editingInfo.dataLastEditDate`
(2026-11-22 when the city was wired: the council's arborists edit the
inventory live).  The stamp is epoch milliseconds, a real UTC instant.

An outage degrades through `get_json_with_retry`; a layer that stops
publishing `editingInfo` raises, because that is a schema change.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit  # noqa: E402
from shared.ingest import emit_freshness  # noqa: E402

LAYER = FeatureLayer(
    "https://services-ap1.arcgis.com/AtixmNNZDz8cwc9l/arcgis/rest/services/"
    "Tree_Inventory_All%20View/FeatureServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("AULTV", fetch_modified_at)

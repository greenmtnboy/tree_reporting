#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Surrey's tree inventory.

The `Park Specimen Trees` layer is a hosted ArcGIS Online feature layer with
editing tracked, so the watermark is its own `editingInfo.dataLastEditDate`
(2026-11-01 when the city was wired; the item says it is updated monthly).
The stamp is epoch milliseconds, a real UTC instant.

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
    "https://services5.arcgis.com/YRpe0VKTJytZSSIB/arcgis/rest/services/"
    "Park%20Specimen%20Trees/FeatureServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("CASRY", fetch_modified_at)

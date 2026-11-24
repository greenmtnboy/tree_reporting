#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Plano's tree inventory.

`Shade_Map_WSL1/FeatureServer/0` is an ArcGIS Online hosted layer and
publishes `editingInfo.lastEditDate`, which `layer_last_edit` reads (an outage
degrades through `emit_freshness`; a layer that stops publishing the stamp
raises, a schema question).  Esri returns it as epoch milliseconds in UTC.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services2.arcgis.com/1DyhsVa6rviDKn5t/arcgis/rest/services/Shade_Map_WSL1/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USPLN", fetch_modified_at)

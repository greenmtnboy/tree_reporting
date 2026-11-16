#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Los Angeles's street-tree inventory.

Reads `editingInfo.dataLastEditDate` off the Bureau of Street Services layer
(see losangeles_tree_info.py).  The layer is a historical inventory the city no
longer maintains, so this has read 2020-11-19 since it was published and a
refresh re-downloads only if the city ever republishes it.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness
from shared.platforms.arcgis import FeatureLayer, layer_last_edit

LAYER = FeatureLayer(
    "https://services5.arcgis.com/7nsPwEMP38bSkCjy/arcgis/rest/services/"
    "Trees_Data_Bureau_of_Street_Services/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USLAX", fetch_modified_at)

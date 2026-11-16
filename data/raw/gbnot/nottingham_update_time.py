#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Nottingham's council-maintained tree layer.

Reads the hosted layer's own `editingInfo.dataLastEditDate` (2026-11-10 when
the city was wired, the day the council last reloaded the layer -- every
row's `Created_Date` is that day too), so a refresh only re-downloads the
45k-row layer when the council has republished it.

`layer_last_edit` raises when the layer stops publishing `editingInfo`, a
schema question that must not degrade to "no new data"; an outage is caught
by `get_json_with_retry` inside it and does degrade.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services.arcgis.com/yvqphKcf9bBSnjX1/arcgis/rest/services/"
    "Trees__NCC_Maintained_/FeatureServer/63"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("GBNOT", fetch_modified_at)

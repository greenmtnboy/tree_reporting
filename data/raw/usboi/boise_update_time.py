#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Boise's park and street trees.

The later of the two hosted layers' own `editingInfo.dataLastEditDate`
(`BPR_Park_And_Street_Trees/FeatureServer/0` park trees, `/1` street trees;
2026-11-21 when the city was wired) -- two metadata responses, so a refresh
only re-downloads the 35k rows when Parks and Recreation has edited either.

`layer_last_edit` raises when a layer stops publishing `editingInfo`, a
schema question that must not degrade to "no new data"; an outage is caught
by `get_json_with_retry` inside it and does degrade.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

SERVICE = (
    "https://services1.arcgis.com/WHM6qC35aMtyAAlN/arcgis/rest/services/"
    "BPR_Park_And_Street_Trees/FeatureServer"
)
LAYERS = (FeatureLayer(f"{SERVICE}/0"), FeatureLayer(f"{SERVICE}/1"))


def fetch_modified_at() -> datetime:
    return max(layer_last_edit(layer) for layer in LAYERS)


if __name__ == "__main__":
    emit_freshness("USBOI", fetch_modified_at)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Belfast's landmarks: the later of the two HED layers'
`editingInfo.dataLastEditDate` (the service is republished monthly)."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

SERVICE = (
    "https://services2.arcgis.com/BdBkthNLO9mzGAMO/arcgis/rest/services/"
    "Historic_Environment_Division_GIS_Data/FeatureServer"
)
LAYERS = (FeatureLayer(f"{SERVICE}/1"), FeatureLayer(f"{SERVICE}/0"))


def fetch_modified_at() -> datetime:
    return max(layer_last_edit(layer) for layer in LAYERS)


if __name__ == "__main__":
    emit_freshness("GBBFS", fetch_modified_at, label="GBBFS landmarks")

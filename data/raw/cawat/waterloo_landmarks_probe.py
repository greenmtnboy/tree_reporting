#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Waterloo's (Ontario) heritage register.

The "Heritage Buildings (Points)" layer publishes `editingInfo`; its
`dataLastEditDate` (2018-02-13 when the city was wired -- the layer is
rarely republished) moves when the City republishes the register.  A layer
that stops publishing it raises.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services.arcgis.com/ZpeBVw5o1kjit7LT/arcgis/rest/services/"
    "HeritageBuildings/FeatureServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("CAWAT", fetch_modified_at, label="CAWAT landmarks")

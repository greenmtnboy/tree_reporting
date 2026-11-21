#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Fredericton's heritage preservation areas.

The "Heritage View OD" layer publishes `editingInfo`; its `dataLastEditDate`
(2026-11-18 when the city was wired) moves with every edit to the areas.  A
layer that stops publishing it raises.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services2.arcgis.com/iLWAxhpxafhOza2U/arcgis/rest/services/"
    "Heritage_View_OD/FeatureServer/69",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("CAFRE", fetch_modified_at, label="CAFRE landmarks")

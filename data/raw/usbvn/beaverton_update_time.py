#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Beaverton's tree inventory: the layer's own last edit
(`editingInfo.lastEditDate`; the layer publishes no `dataLastEditDate`)."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit  # noqa: E402
from shared.ingest import emit_freshness  # noqa: E402

LAYER = FeatureLayer(
    "https://gisweb.beavertonoregon.gov/server/rest/services/Hosted/Tree_Inventory_Hosted/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USBVN", fetch_modified_at)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Bismarck's tree inventory.

Reads the hosted view's own `editingInfo.dataLastEditDate` (2026-11-21 when
the city was wired; a view reports its source layer's edits, and the
Forestry crews edit it in place) -- one metadata response, so a refresh only
re-downloads the 30k-row layer when the City has edited it.

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
    "https://services1.arcgis.com/XxHmL09eFqJWI0gE/arcgis/rest/services/"
    "Trees_public_3694a69174e146b3b4b24d82a90c198a/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USBIS", fetch_modified_at)

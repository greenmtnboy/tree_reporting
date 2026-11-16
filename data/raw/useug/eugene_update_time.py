#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Eugene, OR's public tree inventory.

Reads the hosted view layer's own `editingInfo.dataLastEditDate`
(2026-11-15 when the city was wired) -- one metadata response, so a refresh
only re-downloads the 113k-row layer when Urban Forestry has edited it.  The
layer is a non-editable view of the city's master tree layer, and its
`editingInfo` follows the edits made to that master.

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
    "https://services3.arcgis.com/F7NiRLGNbA2hh7gE/arcgis/rest/services/"
    "Public_Trees_View_1/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USEUG", fetch_modified_at)

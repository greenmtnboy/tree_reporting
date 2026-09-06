#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for New Westminster's tree inventory.

Reads the layer's own `editingInfo.dataLastEditDate` -- one small metadata
response against the 16k-row layer -- so a refresh only re-downloads the
inventory when the city has actually edited it.

`layer_last_edit` raises rather than degrading when a layer publishes no
`editingInfo`, because that is our field mapping being wrong rather than the
portal being down, and `emit_freshness` must not turn it into "no new data".
A genuine outage is caught by `get_json_with_retry` inside it and does
degrade.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, layer_last_edit
from _ingest_shared import emit_freshness

LAYER = FeatureLayer(
    "https://services3.arcgis.com/A7O8YnTNtzRPIn7T/arcgis/rest/services/"
    "Tree_Inventory_(PROD)_4_view/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("CANWE", fetch_modified_at)

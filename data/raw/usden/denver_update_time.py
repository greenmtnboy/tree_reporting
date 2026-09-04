#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Denver's municipal tree inventory.

Reads the layer's own `editingInfo.dataLastEditDate` -- one ~4 KB metadata
response against the 359k-row layer -- so a refresh only re-downloads the
inventory when the city has actually edited it.

`layer_last_edit` raises rather than degrading when the layer publishes no
`editingInfo`: that is our field mapping being wrong, not the portal being
down, and `emit_freshness` must not turn it into "no new data" (which would
freeze Denver's Parquet silently and for ever).  A genuine outage is caught by
`get_json_with_retry` inside it and does degrade.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, layer_last_edit
from _ingest_shared import emit_freshness

LAYER = FeatureLayer(
    "https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/"
    "ODC_PARK_TREEINVENTORY_P/FeatureServer/241"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USDEN", fetch_modified_at)

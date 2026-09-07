#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Moncton's tree inventory.

The layer publishes `editingInfo`, so this is the cheapest of the three
watermarks: one metadata read.  Its `dataLastEditDate` read 2026-08-27 when the
city was wired, three days ahead of the Hub catalogue's own stamp for the same
dataset -- which is the ordinary case for a hosted feature service and the
reason `layer_last_edit` is preferred wherever it exists.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, layer_last_edit
from _ingest_shared import emit_freshness

LAYER = FeatureLayer(
    "https://services1.arcgis.com/E26PuSoie2Y7bbyI/arcgis/rest/services/"
    "Trees/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("CAMON", fetch_modified_at)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Halifax's public tree inventory.

Reads the layer's own `editingInfo.dataLastEditDate` -- one ~10 KB metadata
response against the 80k-row layer -- so a refresh only re-downloads the
inventory when HRM has actually edited it.

The layer also carries a `MODDATE` column, and it is the *worse* watermark
here: its maximum reads 2025-11-21 while `editingInfo` reads 2026-09, so the
column lags the edits it is meant to record.  `field_max` is for layers that
publish no `editingInfo` at all -- Kingston, Lethbridge and Victoria among
this batch.

`layer_last_edit` raises rather than degrading when `editingInfo` is missing,
because that is our field mapping being wrong rather than the portal being
down, and `emit_freshness` must not turn it into "no new data".  A genuine
outage is caught by `get_json_with_retry` inside it and does degrade.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, layer_last_edit
from _ingest_shared import emit_freshness

LAYER = FeatureLayer(
    "https://services2.arcgis.com/11XBiaBYA9Ep0yNJ/arcgis/rest/services/"
    "Public_Trees/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("CAHFX", fetch_modified_at)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Launceston's tree inventory.

`ParksAndRecreation/FeatureServer/1` is a hosted feature layer with
`editingInfo`; its `dataLastEditDate` moves when the council edits or
reloads the trees (2023-02-22 when the city was wired; the newest
`last_edited_date_UTC` on a row is the same day).  Esri stamps are UTC
milliseconds, so no zone handling is needed.  A layer that stops publishing
`editingInfo` raises (a schema question); an outage degrades through
`emit_freshness`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services.arcgis.com/yeXpdyjk3azbqItW/arcgis/rest/services/"
    "ParksAndRecreation/FeatureServer/1"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("AULST", fetch_modified_at)

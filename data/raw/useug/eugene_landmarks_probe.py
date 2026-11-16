#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Eugene, OR's landmarks.

Reads the historic-sites layer's own `editingInfo.dataLastEditDate`
(2026-07-30 when the city was wired), so the weekly landmark lane only
rebuilds Eugene's parquet when the Planning Division has edited the
inventory.  `layer_last_edit` raises when the layer stops publishing
`editingInfo` (a schema change); an outage degrades.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services3.arcgis.com/F7NiRLGNbA2hh7gE/arcgis/rest/services/"
    "Historic_Sites_(public_view)/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USEUG", fetch_modified_at, label="USEUG landmarks")

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Davenport's tree inventory.

The hosted layer `Trees_All/FeatureServer/0` publishes `editingInfo`; its
`dataLastEditDate` moves only when rows change (2022-01-12 in November
2026, the layer's one load), not on a schema-only edit.  `layer_last_edit`
reads it and raises if the block disappears (a schema question); an outage
degrades through `emit_freshness`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit  # noqa: E402
from shared.ingest import emit_freshness  # noqa: E402

LAYER = FeatureLayer(
    "https://services.arcgis.com/Sf3rzzad9Dl4Cuf7/arcgis/rest/services/"
    "Trees_All/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USDVN", fetch_modified_at)

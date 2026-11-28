#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Coral Springs's tree inventory.

The hosted view `Cityworks_Horticulture_Tree_Test_view_Public/FeatureServer/0`
publishes `editingInfo`; its `dataLastEditDate` moves when rows change, not on
a schema-only edit.  `layer_last_edit` reads it and raises if the block
disappears (a schema question); an outage degrades through `emit_freshness`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit  # noqa: E402
from shared.ingest import emit_freshness  # noqa: E402

LAYER = FeatureLayer(
    "https://services1.arcgis.com/mXWZ2wsX9klsJAJY/arcgis/rest/services/"
    "Cityworks_Horticulture_Tree_Test_view_Public/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USCRS", fetch_modified_at)

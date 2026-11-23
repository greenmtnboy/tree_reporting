#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for the City of Unley's street trees.

The hosted layer's own `editingInfo.dataLastEditDate` (2025-08-01 when the
partition was wired): one metadata request, so a refresh only re-reads the
31k rows when the council has republished them.

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

# Kept in step with unley_tree_info.py's LAYER.
LAYER = FeatureLayer(
    "https://services9.arcgis.com/vsQXCXZ0LMZJANxk/arcgis/rest/services/"
    "Trees/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("AUADL", fetch_modified_at)

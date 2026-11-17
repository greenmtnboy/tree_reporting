#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Fingal's Record of Protected Structures.

The layer's own `editingInfo.dataLastEditDate` (2023-09-19 when the city was
wired).  The register is amended by the development plan and its variations,
each republished into this layer, which is what moves the stamp.  The layer
has no per-row edit date.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness
from shared.platforms.arcgis import FeatureLayer, layer_last_edit

LAYER = FeatureLayer(
    "https://services5.arcgis.com/CI1e5PKQXvJgmJK8/arcgis/rest/services/"
    "FCC_Development_Plan_2023_2029_RPS_Record_of_Protected_Structures/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("IEFIN", fetch_modified_at, label="IEFIN landmarks")

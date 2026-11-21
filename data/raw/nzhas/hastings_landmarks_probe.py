#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Hastings's landmarks (District Plan heritage items).

`HeritageFeatures_DistrictPlan/FeatureServer/0` is a hosted layer with
`editingInfo`; its `dataLastEditDate` (UTC) moves when the council edits
the heritage story map's items (2026-03-17 when wired).  A layer that stops
publishing `editingInfo` raises; an outage degrades through `emit_freshness`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services1.arcgis.com/8L3DQUzjrkgEmDpQ/arcgis/rest/services/"
    "HeritageFeatures_DistrictPlan/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("NZHAS", fetch_modified_at, label="NZHAS landmarks")

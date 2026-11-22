#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Penticton's heritage register.

The `Heritage Site` MapServer layer on the City's own server publishes no
`editingInfo` and no edit-date column, so this reads the
`editingInfo.dataLastEditDate` of the City's hosted copy of it
(`Planning_PRD/FeatureServer/235`), which the City's `gisscripts` account
reloads from the server (2026-11-22 when the city was wired).  A reload moves
it whether or not a site changed; the weekly landmark lane bounds what that
costs, and the register is 52 rows.

A layer that loses `editingInfo` raises (a schema change); an outage degrades.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

HOSTED_COPY = FeatureLayer(
    "https://services1.arcgis.com/ZMQyarkhNAnn8lip/arcgis/rest/services/"
    "Planning_PRD/FeatureServer/235",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(HOSTED_COPY)


if __name__ == "__main__":
    emit_freshness("CAPEN", fetch_modified_at, label="CAPEN landmarks")

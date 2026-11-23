#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Prince George's heritage-register landmarks.

The register itself is a web page with no machine-readable stamp; its places
are written in `princegeorge_landmarks.py` and change only with that file.
What can move under them is the City's civic address layer they are placed
on (`OpenData_Address/FeatureServer/4`), a hosted layer with `editingInfo`,
so the watermark is its `dataLastEditDate` (2026-11-06 when the city was
wired).  An outage degrades; a layer that loses `editingInfo` raises.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

ADDRESSES = FeatureLayer(
    "https://services2.arcgis.com/CnkB6jCzAsyli34z/arcgis/rest/services/"
    "OpenData_Address/FeatureServer/4",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(ADDRESSES)


if __name__ == "__main__":
    emit_freshness("CAPRG", fetch_modified_at, label="CAPRG landmarks")

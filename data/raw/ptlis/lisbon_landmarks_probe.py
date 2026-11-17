#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Lisbon's heritage landmarks.

Every layer `lisbon_landmarks.py` reads is a hosted feature layer with
`editingInfo`; the watermark is the latest `dataLastEditDate` across them
(2026-10-30 at writing, when the city republished the whole service).
`layer_last_edit` raises when a layer stops publishing `editingInfo` -- a
schema change -- and degrades on an outage through `emit_freshness`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness
from shared.platforms.arcgis import FeatureLayer, layer_last_edit

SERVICE = "https://services.arcgis.com/1dSrzEWVQn5kHHyK/arcgis/rest/services/Patrimonio/FeatureServer"
LAYER_IDS = (13, 12, 11, 7, 8, 15, 10)  # keep in step with lisbon_landmarks.LAYERS


def fetch_modified_at() -> datetime:
    return max(layer_last_edit(FeatureLayer(f"{SERVICE}/{i}")) for i in LAYER_IDS)


if __name__ == "__main__":
    emit_freshness("PTLIS", fetch_modified_at, label="PTLIS landmarks")

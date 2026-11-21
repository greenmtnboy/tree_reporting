#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Red Deer's historical-site register.

`LUB_HistoricalSite_approved` publishes no `editingInfo`; its rows carry the
City's own `LastModDate` (2023-06-14 when the city was wired), so the
watermark is `MAX(LastModDate)`.  Stored in Canada Central Standard Time and
returned as if UTC, so it reads six hours ahead; only its movement matters.
A missing column raises.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://arcgis.reddeer.ca/arcgis/rest/services/CoRD_View_Layers/"
    "LUB_HistoricalSite_approved/FeatureServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "LastModDate")


if __name__ == "__main__":
    emit_freshness("CARED", fetch_modified_at, label="CARED landmarks")

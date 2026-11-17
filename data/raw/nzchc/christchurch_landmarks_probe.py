#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Christchurch's landmarks (District Plan heritage items).

The layer publishes no `editingInfo`, so MAX(`LastEditDate`) over it, one
statistics query (2026-03-24 when the city was wired).  A plan change that
adds, edits or removes a scheduled item edits its row and moves the stamp.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://gis.ccc.govt.nz/server/rest/services/OpenData/DistrictPlanB/FeatureServer/31"
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "LastEditDate")


if __name__ == "__main__":
    emit_freshness("NZCHC", fetch_modified_at, label="NZCHC landmarks")

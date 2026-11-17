#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Seattle's public trees (SDOT street trees + Parks trees).

The later of the two layers' row-edit stamps:

* **SDOT Trees**: MAX(`MODDATE`) over the whole layer (2026-11-03 when the
  city was wired).  `MODDATE` is the asset's last modification in SDOT's
  asset system, date precision, and moves when a tree is planted, removed or
  re-inspected.  Not `editingInfo`: the hosted layer is reloaded daily, so its
  edit stamp moves every day whether or not a tree changed.
* **SPR Tree View**: MAX(`GIS_EDT_DT`), the Parks editor's own edit stamp
  (2026-11-04).  The view is refreshed weekly, and its `editingInfo` moves on
  every refresh for the same reason.

`field_max` raises when a field is gone or the statistics row is empty, a
schema question that must not degrade to "no new data"; an outage is caught
by `get_json_with_retry` inside it and does degrade.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

SDOT = FeatureLayer(
    "https://services.arcgis.com/ZOyb2t4B0UYuYNYH/arcgis/rest/services/SDOT_Trees_CDL/FeatureServer/0"
)
SPR = FeatureLayer(
    "https://services.arcgis.com/ZOyb2t4B0UYuYNYH/arcgis/rest/services/SPR_Tree_View/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return max(field_max(SDOT, "MODDATE"), field_max(SPR, "GIS_EDT_DT"))


if __name__ == "__main__":
    emit_freshness("USSEA", fetch_modified_at)

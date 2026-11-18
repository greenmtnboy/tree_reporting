#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Wellington's landmarks (2024 District Plan heritage).

The `2024DistrictPlan` MapServer layers publish no `editingInfo` and carry no
edit-date column, so the probe reads the council hub's DCAT `modified` stamp
(`hub_last_modified`) and takes the latest over the three schedules.  The hub
catalogues the SCHED1 buildings layer the script reads (67) directly; for
SCHED2 and SCHED3 it catalogues the sibling layers of the same schedule
(71, structure extents; 74, heritage area points) rather than the ones read
(70, 73), and those stand in: a plan change edits a schedule's layers together.
Stamps when wired: 2025-07-31, 2025-09-08, 2026-03-31.  The plan is
operative and the layers are described as static, so this rarely moves.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import hub_last_modified
from shared.ingest import emit_freshness

HUB_HOST = "data-wcc.opendata.arcgis.com"
BASE = "https://gis.wcc.govt.nz/arcgis/rest/services/2024DistrictPlan/2024DistrictPlan/MapServer"
CATALOGUED_LAYERS = (67, 71, 74)


def fetch_modified_at() -> datetime:
    return max(hub_last_modified(HUB_HOST, f"{BASE}/{layer}") for layer in CATALOGUED_LAYERS)


if __name__ == "__main__":
    emit_freshness("NZWLG", fetch_modified_at, label="NZWLG landmarks")

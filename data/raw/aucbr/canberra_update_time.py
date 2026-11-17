#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Canberra's tree inventory (ACTGOV Tree Assets).

MAX(`LAST_EDITED_DATE`) over the layer: the asset system's own edit stamp,
which moves when a tree is added, removed or re-identified (2026-11-13 when the
city was wired).  Not `editingInfo`: the hosted layer's `dataLastEditDate`
(2026-11-16 the same day) moves when the directorate pushes its sync, whether
or not a tree changed, and each move would re-download 786k rows.

`field_max` raises when the field is gone or the statistics row is empty, a
schema question that must not degrade to "no new data"; an outage is caught by
`get_json_with_retry` inside it and does degrade.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services1.arcgis.com/E5n4f1VY84i0xSjy/arcgis/rest/services/"
    "ACTGOV_Tree_Assets/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "LAST_EDITED_DATE")


if __name__ == "__main__":
    emit_freshness("AUCBR", fetch_modified_at)

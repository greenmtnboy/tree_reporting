#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Oakville's (Ontario) tree inventory.

MAX(`last_edited_date`) over the "Trees" layer (`PGIS_BULK/Parks_Tree_Forestry`
on the Town's own ArcGIS Server): the geodatabase's editor-tracking stamp,
which moves whenever forestry staff edit a tree (2026-11-20 when the city was
wired -- the layer is the Town's working inventory and is edited most days).
The layer publishes no `editingInfo`, and the Hub item's `modified` (2024-05)
does not follow the data, so it is not used.

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
    "https://maps.oakville.ca/oakgis/rest/services/PGIS_BULK/"
    "Parks_Tree_Forestry/FeatureServer/0",
    timeout=120,
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "last_edited_date")


if __name__ == "__main__":
    emit_freshness("CAOAK", fetch_modified_at)

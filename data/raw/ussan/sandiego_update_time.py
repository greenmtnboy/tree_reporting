#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for San Diego's street tree inventory.

MAX(`last_edited_date`) over the City's `Trees (Street Trees)` layer
(2026-06-25 when the city was wired): the editor-tracking stamp the City's
geodatabase writes on every row edit -- a planting, a removal (which flips
`MAPNG_STAT_CD` to `RM`), a re-inspection.  The layer is a MapServer layer
with no `editingInfo`, so `layer_last_edit` has nothing to read.

Esri dates come back as UTC epoch milliseconds; `field_max` returns them
timezone-aware.  `field_max` raises when the field is gone or the statistics
row is empty, a schema question that must not degrade to "no new data"; an
outage is caught by `get_json_with_retry` inside it and does degrade.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://webmaps.sandiego.gov/arcgis/rest/services/DSD/Environment/MapServer/20",
    timeout=180,
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "last_edited_date")


if __name__ == "__main__":
    emit_freshness("USSAN", fetch_modified_at)

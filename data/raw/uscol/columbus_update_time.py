#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Columbus, OH's public tree inventory.

The layer is a MapServer layer with no `editingInfo`, so the probe reads
MAX(`last_edited_date`) over the whole layer with one statistics query
(2026-11-13 when the city was wired; Urban Forestry edits the layer most
working days).  Editor tracking stamps every edit, including a retirement or
a stump-grinding job, so the maximum moves whenever the published set can.

The ArcGIS item's own `modified` (2020-09-02) is not used: it is the
catalogue entry, and has not moved in years while the rows have.

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
    "https://maps2.columbus.gov/arcgis/rest/services/Schemas/RecreationParks/MapServer/1"
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "last_edited_date")


if __name__ == "__main__":
    emit_freshness("USCOL", fetch_modified_at)

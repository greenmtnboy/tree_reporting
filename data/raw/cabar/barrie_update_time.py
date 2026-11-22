#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Barrie's tree inventory.

The layer is a MapServer layer on the City's own ArcGIS Server
(`Open_Data/FacilitiesStreets/MapServer/37`) and publishes no `editingInfo`,
but every row carries `LASTUPDATE`, the asset system's edit stamp (UTC; the
layer reports `datesInUnknownTimezone: false`), and its maximum moves with the
forestry team's edits (2026-09-03 when the city was wired).  `field_max`
reads it in one statistics request; an outage degrades through
`get_json_with_retry`, and a missing field or an empty statistics row raises,
because that is a schema change.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://gispublic.barrie.ca/arcgis/rest/services/Open_Data/"
    "FacilitiesStreets/MapServer/37",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "LASTUPDATE")


if __name__ == "__main__":
    emit_freshness("CABAR", fetch_modified_at)

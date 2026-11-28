#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Loveland's tree inventory.

The source is a MapServer layer on the City's Cityworks server, which
publishes no `editingInfo`, and the layer has no edit-date column.
`InspectDate` is the closest: Cityworks stamps it when a crew edits the
asset (every row was re-stamped on 2026-06-26, and a few hundred a month
since), so MAX(InspectDate) moves when the inventory does.  `field_max`
raises if the field disappears or comes back empty (a schema question); an
outage degrades through `emit_freshness`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max  # noqa: E402
from shared.ingest import emit_freshness  # noqa: E402

LAYER = FeatureLayer(
    "https://cityworks.cityofloveland.org/arcgis/rest/services/"
    "ParksandRec/Trees/MapServer/0"
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "InspectDate")


if __name__ == "__main__":
    emit_freshness("USLVL", fetch_modified_at)

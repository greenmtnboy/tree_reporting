#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Oxnard's tree inventory.

`ParksLayers/MapServer/1` is a map-service layer over the City's enterprise
geodatabase and publishes no `editingInfo` (and has no FeatureServer twin),
so the watermark is the newest `DateModified` (the date field the City stamps
on an edited tree; 2023-12-18 in Nov 2026), read with one statistics query.
Esri returns it as epoch milliseconds, and a few hours of zone offset make no
difference to a staleness check.  A missing statistics row raises (a schema
question); an outage degrades through `emit_freshness`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://maps.oxnard.org/arcgis/rest/services/ParksLayers/MapServer/1"
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "DateModified")


if __name__ == "__main__":
    emit_freshness("USOXR", fetch_modified_at)

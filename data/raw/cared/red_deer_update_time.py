#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Red Deer's tree inventory.

The `PARK_Trees_inservice` view publishes no `editingInfo`, but every row
carries the City's own edit stamp `LastModDate` (set by the forestry editors;
2026-11-20 when the city was wired -- the inventory is edited most working
days), so the watermark is `MAX(LastModDate)` via `field_max`.

The layer's `dateFieldsTimeReference` is Canada Central Standard Time
(Etc/GMT+6, no daylight saving), and ArcGIS returns the stored wall-clock
time as if it were UTC, so this watermark runs six hours ahead of the real
instant.  It only has to move when the data does, which it does.

An outage degrades through `get_json_with_retry`; a layer that loses the
column or returns no statistics row raises, because that is a schema change.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://arcgis.reddeer.ca/arcgis/rest/services/CoRD_View_Layers/"
    "PARK_Trees_inservice/FeatureServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "LastModDate")


if __name__ == "__main__":
    emit_freshness("CARED", fetch_modified_at)

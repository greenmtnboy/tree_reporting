#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Sioux Falls's tree inventory.

`Data/Trees/MapServer/10` is a map-service layer over the City's enterprise
geodatabase and publishes no `editingInfo`, so the watermark is the newest
`last_edited_date` (the geodatabase's editor-tracking stamp), read with one
statistics query.  Forestry edits the layer most working days, so this moves
often; the rebuild it triggers is a single 85k-row read.  Esri returns the
stamp as epoch milliseconds (the layer declares no time reference), and a
few hours of zone offset make no difference to a staleness check.  A missing
statistics row raises (a schema question); an outage degrades through
`emit_freshness`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://gis.siouxfalls.gov/arcgis/rest/services/Data/Trees/MapServer/10"
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "last_edited_date")


if __name__ == "__main__":
    emit_freshness("USFSD", fetch_modified_at)

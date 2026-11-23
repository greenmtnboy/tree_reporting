#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Grand Junction's tree inventory.

`Hub/City_Hub/MapServer/1` is a map-service layer over the City's enterprise
geodatabase and publishes no `editingInfo`, so the watermark is the newest
`last_edited_date` (the geodatabase's editor-tracking stamp), read with one
statistics query.  The layer is synced from Cartegraph and the stamp moves
most days; the rebuild it triggers is a single 16k-row read.  Esri returns
the stamp as epoch milliseconds, and a few hours of zone offset make no
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
    "https://external25-gis.gjcity.org/arcgis/rest/services/Hub/City_Hub/MapServer/1"
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "last_edited_date")


if __name__ == "__main__":
    emit_freshness("USGJT", fetch_modified_at)

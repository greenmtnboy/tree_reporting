#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Barrie's heritage register.

The `Heritage Properties` layer (a MapServer layer on the City's own ArcGIS
Server) publishes no `editingInfo`; each row carries `LASTUPDATE`, and its
maximum over the register rows is the watermark (2026-11-19 when the city was
wired).  `field_max` degrades on an outage and raises on a missing field,
which is a schema change.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://gispublic.barrie.ca/arcgis/rest/services/Public/"
    "OperationalLayers_Dynamic/MapServer/54",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "LASTUPDATE", where="STATUS IN ('DESIGNATED','LISTED')")


if __name__ == "__main__":
    emit_freshness("CABAR", fetch_modified_at, label="CABAR landmarks")

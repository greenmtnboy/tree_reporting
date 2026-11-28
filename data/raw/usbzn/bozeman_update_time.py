#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Bozeman's tree inventory.

The layer is a MapServer layer over a versioned geodatabase and publishes no
`editingInfo`, so the probe is `MAX(last_edited_date)` over the public layer
(`field_max`): Forestry staff edit it most working days (2026-11-25 in late
November 2026).  A removal moves the tree out of the public layer (it becomes
a `removal site` row) and so does not move this stamp by itself; the next
edit to any standing tree does.  A missing field raises (a schema question);
an outage degrades through `emit_freshness`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max  # noqa: E402
from shared.ingest import emit_freshness  # noqa: E402

LAYER = FeatureLayer(
    "https://gisweb.bozeman.net/arcgis/rest/services/Public/"
    "Forestry_Public_Web_Map/MapServer/2"
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "last_edited_date")


if __name__ == "__main__":
    emit_freshness("USBZN", fetch_modified_at)

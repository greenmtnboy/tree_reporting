#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Durham, NC's tree inventory.

The layer is an enterprise-geodatabase service with editor tracking but no
`editingInfo`, so the watermark is `MAX(last_edited_date)` over the layer, one
statistics query.  It read 2026-11 when the city was wired; Urban Forestry
edits the layer through the planting season.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://webgis2.durhamnc.gov/server/rest/services/"
    "PublicServices/Environmental/FeatureServer/11"
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "last_edited_date")


if __name__ == "__main__":
    emit_freshness("USDUR", fetch_modified_at)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for East Lansing's tree inventory.

The layer publishes no `editingInfo` and no edit-date column, so the probe is
the newest of the dates the City writes when it touches a tree: an
inspection (`elDate`), a note (`TNDate`), the inventory date (`InvDate`) and a
planting (`PlantDate`).  A deletion moves none of them; the weekly OSM extract
re-reads the city regardless.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max  # noqa: E402
from shared.ingest import emit_freshness  # noqa: E402

LAYER = FeatureLayer(
    "https://gis2.cityofeastlansing.com/arcgis/rest/services/TREES_STATUS/FeatureServer/0"
)
DATE_FIELDS = ("elDate", "TNDate", "InvDate", "PlantDate")


def fetch_modified_at() -> datetime:
    return max(field_max(LAYER, field) for field in DATE_FIELDS)


if __name__ == "__main__":
    emit_freshness("USELN", fetch_modified_at)

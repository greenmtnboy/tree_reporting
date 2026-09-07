#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Ottawa's heritage cultural spaces.

The layer publishes no `editingInfo`, but carries `LAST_EDITED_DATE`.  Every
row currently holds the same stamp, which is what a bulk reload of the
inventory looks like; it is still a real watermark and moves the next time the
city reloads it.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, field_max
from _ingest_shared import emit_freshness

LAYER = FeatureLayer(
    "https://maps.ottawa.ca/arcgis/rest/services/OfficialPlan/MapServer/133"
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "LAST_EDITED_DATE")


if __name__ == "__main__":
    emit_freshness("CAOTT", fetch_modified_at, label="CAOTT landmarks")

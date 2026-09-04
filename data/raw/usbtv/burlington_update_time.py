#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Burlington's tree inventory.

The layer publishes no `editingInfo`, so the watermark is MAX(EditDate) via an
`outStatistics` query -- one row rather than the table.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, field_max
from _ingest_shared import emit_freshness

LAYER = FeatureLayer(
    "https://maps.burlingtonvt.gov/arcgis/rest/services/"
    "Tree_Sites_Public_View/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "EditDate")


if __name__ == "__main__":
    emit_freshness("USBTV", fetch_modified_at)

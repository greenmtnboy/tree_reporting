#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for New Orleans, LA's HDLC landmark register.

The MapServer layer publishes no `editingInfo`, but every row carries an
`EditDate`, so the watermark is MAX(EditDate) and the weekly landmark lane only
rebuilds New Orleans's parquet when the register has actually been edited.  It
read 2026-11-06 when the city was wired.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://maps.nola.gov/server/rest/services/Administrative/Historic_Preservation/MapServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "EditDate")


if __name__ == "__main__":
    emit_freshness("USNOL", fetch_modified_at, label="USNOL landmarks")

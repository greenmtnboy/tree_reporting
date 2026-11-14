#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for New Orleans, LA's tree inventory.

The 311 asset service's Trees layer (`OpenGov/OpenGov311_Assets/MapServer/7`
on maps.nola.gov) is a MapServer layer and publishes no `editingInfo`, but it
carries the editor-tracking column `last_edited_date`, so the watermark is
MAX(last_edited_date): one statistics row, not the table.  It read 2026-09-15
when the city was wired (2026-11-11); Parks and Parkways crews update
conditions and diameters through the year.

The open-data "Tree Location" export (data.nola.gov `3tyj-9x33`) is not the
watermark: its `rowsUpdatedAt` has not moved since 2025-04-17.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://maps.nola.gov/server/rest/services/OpenGov/OpenGov311_Assets/MapServer/7",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "last_edited_date")


if __name__ == "__main__":
    emit_freshness("USNOL", fetch_modified_at)

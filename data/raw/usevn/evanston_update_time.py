#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Evanston, Illinois's tree inventory.

The layer (`OpenData/ArcGISOpenData/MapServer/8`) is a MapServer layer with
no `editingInfo`, but it carries the geodatabase's editor-tracking column
`last_edited_date`, set on every row and moved by the City's crews' edits
(2026-11-10 when the city was wired), so its MAX is the layer's last edit.

An outage degrades through `get_json_with_retry`; a layer that loses the
column, or returns no statistics row, raises (`field_max`).
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://maps.cityofevanston.org/arcgis/rest/services/OpenData/"
    "ArcGISOpenData/MapServer/8",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "last_edited_date")


if __name__ == "__main__":
    emit_freshness("USEVN", fetch_modified_at)

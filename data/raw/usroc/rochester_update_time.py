#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Rochester, NY's tree inventory.

The ingest reads the City's `Open_Data/Trees_Open_Data/FeatureServer/0`, an
ArcGIS Server layer with no `editingInfo` and no edit-date column.  The
newest `INV_DATE` -- the date Forestry last inventoried a tree, set on every
visit and every new planting -- is the watermark (2026-11-20 when the city
was wired: the layer is a live view, edited most working days).  A few rows
carry a mistyped future date (one reads 3035), so only dates before now
count.  A removal that deletes a row does not move it; the next visit to any
tree does.

An outage degrades through `get_json_with_retry`; a layer that stops
answering the statistics query raises (`field_max`).
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://maps.cityofrochester.gov/server/rest/services/Open_Data/"
    "Trees_Open_Data/FeatureServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "INV_DATE", where="INV_DATE < CURRENT_TIMESTAMP")


if __name__ == "__main__":
    emit_freshness("USROC", fetch_modified_at)

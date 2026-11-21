#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Riverside, CA's tree inventory.

The ingest reads four hosted layers on the City's ArcGIS Online (street,
utility-line, miscellaneous and park trees; see riverside_tree_info.py).
Each declares `editingInfo`, and `dataLastEditDate` moves when its rows are
reloaded -- the layers are replaced whole from the City's work-management
export (all four were stamped April 2023 when the city was wired).  The
probe is the latest of the four.  The rows carry no edit-date column, so
there is nothing finer to read.

An outage degrades through `get_json_with_retry`; a layer that stops
publishing `editingInfo` raises (`layer_last_edit`), since that is a schema
change.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

SERVICES = "https://services.arcgis.com/Fu2oOWg1Aw7azh41/arcgis/rest/services"
LAYERS = tuple(
    FeatureLayer(f"{SERVICES}/{name}/FeatureServer/0", timeout=60)
    for name in ("Street_Trees", "Trees_Along_Utility_Lines", "Miscellaneous_Trees", "Park_Trees")
)


def fetch_modified_at() -> datetime:
    return max(layer_last_edit(layer) for layer in LAYERS)


if __name__ == "__main__":
    emit_freshness("USRAL", fetch_modified_at)

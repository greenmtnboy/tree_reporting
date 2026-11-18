#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Louisville's Urban Forestry trees.

The layer is a hosted feature-layer view with `editingInfo`, so the probe reads
`dataLastEditDate` (when any row last changed) from the layer resource; the
view reports its source layer's edits.  Urban Forestry edits the layer most
working days (proposed sites are added and planted ones flipped to
`StreetTree`), so a Status change that adds or drops a tree moves it.

`layer_last_edit` raises when `editingInfo` disappears (a schema question that
must not degrade); an outage is caught by `get_json_with_retry` inside it and
does degrade to the epoch.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services1.arcgis.com/79kfd2K6fskCAkyg/arcgis/rest/services/"
    "URBAN_FORESTRY_TREES_(PUBLIC_DATA_VIEW)/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USLOU", fetch_modified_at)

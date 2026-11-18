#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Sydney's tree inventory.

The City of Sydney's `Trees` layer is a hosted feature layer with
`editingInfo`, so this reads its `dataLastEditDate` (`layer_last_edit`).  The
City overwrites the layer nightly, so the stamp moves every day and AUSYD
rebuilds on each scheduled tick -- ~50k rows over 25 small pages, cheap
enough, and there is no finer signal.

A transport failure degrades to the epoch through `emit_freshness`; a layer
that stops publishing `editingInfo` raises, because that is a schema change.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness
from shared.platforms.arcgis import FeatureLayer, layer_last_edit

LAYER = FeatureLayer(
    "https://services1.arcgis.com/cNVyNtjGVZybOQWZ/arcgis/rest/services/Trees/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("AUSYD", fetch_modified_at)

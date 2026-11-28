#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Sarasota County's tree inventory.

The hosted layer `Hosted/TreeInventory/FeatureServer/0` on the County's
ArcGIS Enterprise server publishes `editingInfo.lastEditDate` (no
`dataLastEditDate`); it moves when crews edit the layer, which they do
through the year (2026-11-22 when this was written, with inspections to
2026-11-19).  `layer_last_edit` reads it and raises if the block disappears
(a schema question); an outage degrades through `emit_freshness`.  The
layer's `lastupdate` and `createdate` columns are empty and cannot serve.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit  # noqa: E402
from shared.ingest import emit_freshness  # noqa: E402

LAYER = FeatureLayer(
    "https://ags3.scgov.net/server/rest/services/Hosted/TreeInventory/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USSRQ", fetch_modified_at)

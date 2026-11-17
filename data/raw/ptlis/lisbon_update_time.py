#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Lisbon's tree inventory (Arvoredo).

The layer is a hosted ArcGIS Online feature layer with `editingInfo`, so the
probe reads its `dataLastEditDate` (2026-11-13 when the city was wired; the
catalogue says the register is updated every two months, "Bimestral").  That
stamp moves on any row edit, including a tree marked `Eliminada` or `Cepo`,
so it moves whenever the published set can.

`layer_last_edit` raises when `editingInfo` disappears, a schema question that
must not degrade to "no new data"; an outage is caught by
`get_json_with_retry` inside it and does degrade.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services.arcgis.com/1dSrzEWVQn5kHHyK/arcgis/rest/services/Ambiente_DMEVAE/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("PTLIS", fetch_modified_at)

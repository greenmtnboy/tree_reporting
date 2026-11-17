#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Fingal's tree inventory (Trees_FCC).

The layer's own `editingInfo.dataLastEditDate` (2023-02-01 when the city was
wired).  The layer carries no per-row edit date to take a `field_max` of; a
republish by the council moves the stamp.

`layer_last_edit` raises when `editingInfo` is gone (a schema question that
must not degrade to "no new data"); an outage is caught by
`get_json_with_retry` inside it and degrades to the epoch.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness
from shared.platforms.arcgis import FeatureLayer, layer_last_edit

LAYER = FeatureLayer(
    "https://services5.arcgis.com/CI1e5PKQXvJgmJK8/arcgis/rest/services/"
    "Trees_FCC/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("IEFIN", fetch_modified_at)

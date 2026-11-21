#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Edinburgh's tree layer.

The hosted layer (`Trees/FeatureServer/41` in the City of Edinburgh Council's
ArcGIS Online organisation) publishes `editingInfo`, so this is
`layer_last_edit`: `dataLastEditDate`, the moment the rows last changed.  It
was 2024-06-11 when the city was wired -- the day the council re-hosted the
table -- and moves only when the council republishes it.

`layer_last_edit` goes through `get_json_with_retry`, so an outage degrades
to the epoch; a layer that stops publishing `editingInfo` raises.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://services-eu1.arcgis.com/FgpikkYuSUOuITxp/arcgis/rest/services/"
    "Trees/FeatureServer/41",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("GBEDI", fetch_modified_at)

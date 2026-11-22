#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Maple Ridge's street-tree inventory.

The `Street Tree` layer is a MapServer layer on the City's own ArcGIS Server
and publishes no `editingInfo`, so the watermark is MAX(`last_edited_date`),
the geodatabase's editor-tracking column (2026-11-20 when the city was wired:
the City's arborists edit it weekly).  The layer declares
`dateFieldsTimeReference` Pacific Time, so the server converts the stored
local stamps and the epoch milliseconds it returns are a real UTC instant.

A tree that leaves the published layer (deleted, or set to a `Status` other
than Existing, which the service does not publish) takes its stamp with it,
so it does not move the maximum; the next edit anywhere does, and the layer
is edited most weeks.

An outage degrades through `get_json_with_retry`; a layer that loses the
column raises (the statistics query answers with an error envelope naming the
field, or no row), because that is a schema change.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://geoservices.mapleridge.ca/server/rest/services/"
    "DataCatalog/Environment/MapServer/5",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "last_edited_date")


if __name__ == "__main__":
    emit_freshness("CAMPR", fetch_modified_at)

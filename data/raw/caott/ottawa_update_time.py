#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Ottawa's tree inventory.

The layer publishes no `editingInfo`, but it does carry its own edit-date
column (`MODIFYDATE`, aliased "Modified Date"), so this is the second of the
three watermarks rather than the Hub-catalogue fallback.  It reads a live
stamp -- 2026-09-03 when the city was wired, against a Hub `modified` of
2026-03-27 -- which is exactly why a column beats the catalogue where one
exists.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, field_max
from _ingest_shared import emit_freshness

LAYER = FeatureLayer(
    "https://maps.ottawa.ca/arcgis/rest/services/Forestry/MapServer/0"
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "MODIFYDATE")


if __name__ == "__main__":
    emit_freshness("CAOTT", fetch_modified_at)

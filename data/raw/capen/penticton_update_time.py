#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Penticton's tree inventory.

The ingest reads the `Trees` MapServer layer on the City's own ArcGIS Server,
which publishes no `editingInfo` and no editor-tracking column (its dates are
`PLANTDATE` and `INSTALLDATE`, which say when a tree went in, not when a row
changed).  So the watermark is the later of:

* the `editingInfo.dataLastEditDate` of the City's hosted copy of the same
  table (Open Penticton's "City Owned Trees",
  `services1.arcgis.com/ZMQyarkhNAnn8lip/.../Parks_PRD/FeatureServer/1355`),
  which the City's `gisscripts` account reloads from the server -- 2026-11-22
  10:15 UTC when the city was wired, the small hours Pacific.  A reload moves
  it whether or not a tree changed, which costs at most one rebuild of a
  ten-thousand-row city per scheduled tick;
* MAX(`INSTALLDATE`) on the server layer itself (stamped when a tree is
  entered), so a hosted copy that stops being refreshed cannot freeze the
  city while trees are still being added.

An outage degrades through `get_json_with_retry`; a layer that loses
`editingInfo` or the column raises, because that is a schema change.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max, layer_last_edit
from shared.ingest import emit_freshness

SERVER_LAYER = FeatureLayer(
    "https://maps.penticton.ca/utility/rest/services/External_PRD/Parks/MapServer/1355",
    timeout=60,
)
HOSTED_COPY = FeatureLayer(
    "https://services1.arcgis.com/ZMQyarkhNAnn8lip/arcgis/rest/services/"
    "Parks_PRD/FeatureServer/1355",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return max(layer_last_edit(HOSTED_COPY), field_max(SERVER_LAYER, "INSTALLDATE"))


if __name__ == "__main__":
    emit_freshness("CAPEN", fetch_modified_at)

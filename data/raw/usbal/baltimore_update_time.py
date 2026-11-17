#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Baltimore's tree inventory.

The ingest reads `Foresty/TreeInventory_Public/MapServer/0`, which publishes
no `editingInfo` and no date column.  The same feature class is served as
Forestry's editing service, `Foresty/Trees/FeatureServer/0` (same row count,
same OBJECTIDs, 193,007 rows on 2026-11-16), whose layer declares
`editFieldsInfo` -- `created_date` and `last_edited_date`, stamped by the
geodatabase on every insert and update.  The probe is the later of the two
maxima over that layer (2026-07-27 when the city was wired).  A deleted row
moves neither; a deletion without any other edit is the one change the
probe misses until the next edit.

An outage degrades through `get_json_with_retry`; a layer that no longer has
the two columns, or answers MAX with nothing, raises (`field_max`).
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

EDIT_LAYER = FeatureLayer(
    "https://gis.baltimorecity.gov/egis/rest/services/Foresty/Trees/FeatureServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return max(
        field_max(EDIT_LAYER, "last_edited_date"),
        field_max(EDIT_LAYER, "created_date"),
    )


if __name__ == "__main__":
    emit_freshness("USBAL", fetch_modified_at)

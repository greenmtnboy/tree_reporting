#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Burlington, Ontario's heritage register.

The layer publishes no `editingInfo` but carries `LAST_EDITED_DATE`.  Every
row currently holds the same stamp, which is what a bulk reload of the
register looks like; it is still a real watermark and moves the next time the
city reloads.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, field_max
from _ingest_shared import emit_freshness

LAYER = FeatureLayer(
    "https://mapping.burlington.ca/arcgisweb/rest/services/COB/"
    "HeritageProperties/MapServer/0"
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "LAST_EDITED_DATE")


if __name__ == "__main__":
    emit_freshness("CABUR", fetch_modified_at, label="CABUR landmarks")

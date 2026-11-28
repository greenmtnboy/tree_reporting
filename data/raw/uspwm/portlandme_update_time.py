#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Portland, Maine's tree inventory.

`ParksRec/Trees/MapServer/1` on gis.portlandmaine.gov publishes no
`editingInfo` (a MapServer layer over a versioned enterprise geodatabase),
so this is `field_max` over the layer's own editor-tracking column
`EditDate` (2024-09-19 on 2026-11-27).  A missing statistics row raises (a
schema question); an outage degrades through `emit_freshness`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max  # noqa: E402
from shared.ingest import emit_freshness  # noqa: E402

LAYER = FeatureLayer(
    "https://gis.portlandmaine.gov/maps/rest/services/ParksRec/Trees/MapServer/1"
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "EditDate")


if __name__ == "__main__":
    emit_freshness("USPWM", fetch_modified_at)

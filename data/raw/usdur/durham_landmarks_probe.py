#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Durham, NC's historic landmark registers.

Neither Planning layer publishes `editingInfo`.  The Local Historic Landmarks
layer carries editor tracking, so the watermark is `MAX(EDITDATE)` over it
(2025-10-14 when the city was wired).  The National Register layer's
`EDITDATE` and `CREATIONDATE` are null on every row, so it has no watermark of
its own: a change to it is picked up with the next local-register edit.  The
National Register in Durham changes a few times a decade.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

LOCAL_LANDMARKS = FeatureLayer(
    "https://webgis2.durhamnc.gov/server/rest/services/PublicServices/Planning/MapServer/1"
)


def fetch_modified_at() -> datetime:
    return field_max(LOCAL_LANDMARKS, "EDITDATE")


if __name__ == "__main__":
    emit_freshness("USDUR", fetch_modified_at, label="USDUR landmarks")

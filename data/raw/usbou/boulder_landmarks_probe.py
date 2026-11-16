#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Boulder, CO's historic landmark register.

**The catalogue stamp, because the layer has nothing inside it to read.**
`plan/HistoricLandmarks/MapServer/0` is an on-prem MapServer layer with no
`editingInfo` and no edit-date column, so neither `layer_last_edit` nor
`field_max` applies.  What remains is the `modified` stamp the city's Hub
(open-data.bouldercolorado.gov) publishes for the "Historic Landmarks"
dataset (2025-05-23 when the city was wired) -- `hub_last_modified`, the last
resort of the three watermarks.

Know its failure mode: it is the item's stamp, and it need not move when the
city overwrites the service in place (the tree layer's item stamp is
2025-06-06 while its rows were edited 2026-11-13).  A register that gains a
landmark a year is a small cost to that; if Boulder ever adds an edit-date
column, take the maximum of the two, as Charlotte does.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import hub_last_modified
from shared.ingest import emit_freshness

HUB_HOST = "open-data.bouldercolorado.gov"
LAYER_URL = (
    "https://gis.bouldercolorado.gov/ags_svr1/rest/services/plan/HistoricLandmarks/MapServer/0"
)


def fetch_modified_at() -> datetime:
    return hub_last_modified(HUB_HOST, LAYER_URL)


if __name__ == "__main__":
    emit_freshness("USBOU", fetch_modified_at, label="USBOU landmarks")

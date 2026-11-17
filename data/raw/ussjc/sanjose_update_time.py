#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for San José, CA's street tree inventory.

The layer is a MapServer layer with no `editingInfo`, so the probe reads
MAX(`LASTUPDATE`) over the whole layer with one statistics query (2026-11-15
when the city was wired; the city's arborists edit the layer most weeks).
`LASTUPDATE` is stamped on every row (213k of them with the 2020-08-12
migration), and moves with every edit, including the one that marks a site
vacant after a removal, so the maximum moves whenever the published set can.

The ArcGIS item's own `modified` is not used: it is the catalogue entry, and
moves on republication rather than on edits.

`field_max` raises when the field is gone or the statistics row is empty, a
schema question that must not degrade to "no new data"; an outage is caught by
`get_json_with_retry` inside it and does degrade.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://geo.sanjoseca.gov/server/rest/services/OPN/OPN_OpenDataService/MapServer/510"
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "LASTUPDATE")


if __name__ == "__main__":
    emit_freshness("USSJC", fetch_modified_at)

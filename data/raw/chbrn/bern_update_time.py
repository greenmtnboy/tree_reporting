#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Bern's tree cadastre (Baumkataster).

MAX(`Letzte_Aenderung`) over the geoportal layer: the register's own per-tree
last-change stamp (Esri epoch milliseconds, UTC), 2026-11-17 when the city
was wired, and moving as Stadtgrün edits trees.  The layer is a MapServer
layer with no `editingInfo`, and the opendata.swiss record's `modified` is
only a date, so the column is the watermark.

`field_max` raises when the field is gone or the statistics row is empty (a
schema change, which must not freeze the city); an outage is caught by
`get_json_with_retry` inside it and degrades to the epoch.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness
from shared.platforms.arcgis import FeatureLayer, field_max

LAYER = FeatureLayer(
    "https://map.bern.ch/arcgis/rest/services/Geoportal/Baumkataster/MapServer/0"
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "Letzte_Aenderung")


if __name__ == "__main__":
    emit_freshness("CHBRN", fetch_modified_at)

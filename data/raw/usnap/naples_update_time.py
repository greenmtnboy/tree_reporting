#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Naples, FL's tree inventory.

The City Trees layer is served from the city's own ArcGIS Server (10.51) and
publishes no `editingInfo`, but it carries editor-tracking columns, so the
watermark is the newest `last_edited_date`.  It read 2024-03-05 when the city
was wired (2026-11-11): the inventory is edited in bursts and had not moved
for two and a half years.

Not `shared.platforms.arcgis.field_max`: this server answers every
`outStatistics` query on the layer (FeatureServer and MapServer alike, though
it advertises `supportsStatistics`) with `400: Unable to complete operation.`
inside an HTTP 200, which the shared fetch classifies as unavailability, so
the probe would emit the epoch for ever and the city would never rebuild.  The
same watermark is one row ordered by the column descending.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_datetime
from shared.ingest import emit_freshness, get_json_with_retry

LAYER = FeatureLayer(
    "https://g.naplesgov.com/arcgis/rest/services/"
    "Community_Services/CityTrees/FeatureServer/0"
)

EDIT_FIELD = "last_edited_date"


def fetch_modified_at() -> datetime:
    payload = get_json_with_retry(
        LAYER.query_url,
        params={
            "where": f"{EDIT_FIELD} IS NOT NULL",
            "outFields": EDIT_FIELD,
            "orderByFields": f"{EDIT_FIELD} DESC",
            "resultRecordCount": "1",
            "returnGeometry": "false",
            "f": "json",
        },
        timeout=LAYER.timeout,
    )
    features = payload.get("features") or []
    if not features:
        raise RuntimeError(f"no {EDIT_FIELD} row returned by {LAYER.base}")
    stamp = esri_ms_to_datetime((features[0].get("attributes") or {}).get(EDIT_FIELD))
    if stamp is None:
        raise RuntimeError(f"{EDIT_FIELD} came back empty on {LAYER.base}")
    return stamp


if __name__ == "__main__":
    emit_freshness("USNAP", fetch_modified_at)

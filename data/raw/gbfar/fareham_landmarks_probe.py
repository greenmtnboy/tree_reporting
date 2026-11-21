#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Fareham's landmarks (Historic England's NHLE).

Not the layers' `editingInfo`: Historic England republishes the whole list
daily, so `dataLastEditDate` moves every day for every county and would
rebuild Fareham's parquet each time the landmark lane runs.  What moves
only when Fareham's entries change is the newest designation or amendment
date among them, so this is the latest `ListDate`/`AmendDate` (listed
buildings), `SchedDate`/`AmendDate` (scheduled monuments) and
`RegDate`/`AmendDate` (registered parks and gardens) inside the bounding box
of Fareham's district boundary -- the box, not the boundary, so a
correction that moves an entry across it still counts.  A delisting removes a
row without moving a date and is picked up at the next change; that is the
accepted cost of not rebuilding daily.

`iter_features` routes through `get_json_with_retry`, so an outage degrades
to the epoch; no dates at all raises, since that means the fields were
renamed.  The newest stamp was 2025-05-30 when the city was wired.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_datetime, iter_features
from shared.ingest import emit_freshness

NHLE = (
    "https://services-eu1.arcgis.com/ZOdPfBS3aqqDYPUQ/arcgis/rest/services/"
    "National_Heritage_List_for_England_NHLE_v02_VIEW/FeatureServer"
)
LAYERS = (
    (FeatureLayer(f"{NHLE}/0"), ("ListDate", "AmendDate")),
    (FeatureLayer(f"{NHLE}/6"), ("SchedDate", "AmendDate")),
    (FeatureLayer(f"{NHLE}/7"), ("RegDate", "AmendDate")),
)

# The bounding box of ONS's E07000087 boundary (fareham_landmarks.py reads
# the boundary itself): lon_min, lat_min, lon_max, lat_max.
BOX = "-1.3093,50.8085,-1.1111,50.8988"


def fetch_modified_at() -> datetime:
    box = {
        "geometry": BOX,
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }
    stamps = [
        esri_ms_to_datetime((feature.get("attributes") or {}).get(field))
        for layer, fields in LAYERS
        for page in iter_features(layer, out_fields="OBJECTID," + ",".join(fields), extra_params=box)
        for feature in page
        for field in fields
    ]
    stamps = [s for s in stamps if s is not None]
    if not stamps:
        raise RuntimeError("no NHLE designation or amendment date inside Fareham's box")
    return max(stamps)


if __name__ == "__main__":
    emit_freshness("GBFAR", fetch_modified_at, label="GBFAR landmarks")

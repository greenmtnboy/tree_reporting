#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Sarasota County's landmarks.

The newest `EDIT_DATE` over the NPS National Register rows with
`State = FLORIDA` and `County = Sarasota` (points and polygons) -- the same
cut the landmark script reads, so the weekly landmark lane rebuilds the
County's parquet only when a listing in it has changed.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import esri_ms_to_datetime, iter_features  # noqa: E402
from shared.ingest import emit_freshness  # noqa: E402
from shared.nrhp import NRHP_LAYERS  # noqa: E402

COUNTY_WHERE = "State = 'FLORIDA' AND County = 'Sarasota'"


def fetch_modified_at() -> datetime:
    stamps = [
        esri_ms_to_datetime((feature.get("attributes") or {}).get("EDIT_DATE"))
        for layer in NRHP_LAYERS
        for page in iter_features(layer, out_fields="OBJECTID,EDIT_DATE", where=COUNTY_WHERE)
        for feature in page
    ]
    stamps = [s for s in stamps if s is not None]
    if not stamps:
        raise RuntimeError("no National Register EDIT_DATE in Sarasota County")
    return max(stamps)


if __name__ == "__main__":
    emit_freshness("USSRQ", fetch_modified_at, label="USSRQ landmarks")

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Fareham's tree layer.

**The layer publishes no stamp.**  `Corporate/Trees/MapServer/1` is a
MapServer layer with no `editingInfo`, and its only date-like columns
(`LASTVISIT`, `VISITDATE`) are null on every row, so `field_max` has nothing
to read.  The service's ArcGIS Online item (5f3ea634a36e4f3cb3c9a9e322512211)
has a `modified` time, but it reads 2026-03-09, the day the council touched
the metadata of a dozen unrelated items, so it follows the item rather than
the data.  `OBJECTID` is a dense run that a reload renumbers, which says the
layer changed but not when.

So the watermark is **the start of the current week** (Monday 00:00 UTC), as
Providence's (`uspvd/`) is, read only after the layer has answered and still
has the shape the ingest reads.  It is not a claim that the inventory
changed: it bounds how stale the published parquet can be to a week, at the
cost of one re-read of ~23k rows (12 pages) a week.

The check is the layer's field list and its row count: a layer that loses a
column the ingest reads, or answers zero rows, is the portal changing shape
and raises (it must not freeze the city silently); a transport failure
degrades to the epoch through `emit_freshness`.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, feature_count, layer_metadata
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://maps.fareham.gov.uk/server/rest/services/Corporate/Trees/MapServer/1",
    timeout=60,
)

# The columns fareham_tree_info.py reads.
REQUIRED_FIELDS = frozenset({"OBJECTID", "TREECODE", "SPECIES", "TRUNK", "SPREAD"})


def check_layer() -> None:
    fields = {f.get("name") for f in layer_metadata(LAYER).get("fields") or []}
    missing = REQUIRED_FIELDS - fields
    if missing:
        raise RuntimeError(f"{LAYER.base} lacks {sorted(missing)} (has {sorted(fields)})")
    if feature_count(LAYER) <= 0:
        raise RuntimeError(f"{LAYER.base} answers no rows")


def fetch_modified_at() -> datetime:
    check_layer()
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=today.weekday())


if __name__ == "__main__":
    emit_freshness("GBFAR", fetch_modified_at)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for St. Louis Park's tree inventory.

**The layer publishes no edit stamp.**  `AGO/Parks/MapServer/1` (read through
the item's ArcGIS Online service proxy) is a MapServer layer with no
`editingInfo` and no editor-tracking columns; the item's `modified` time is
2021-11-08, the day the proxy item was made, and the service item behind it
is private.  Its one date column is `RETIRED`, the day a tree was removed,
which moves with removals (2026-11-24 when the city was wired) but not with
plantings or corrections.

So the watermark is **the later of the start of the current week** (Monday
00:00 UTC, as Fareham's and Providence's are) **and the newest `RETIRED`**
(capped at now, in case a removal is entered ahead of the day): a removal
rebuilds the city on the next tick, anything else within a week, at the cost
of one re-read of ~24k rows (25 pages) a week.

The layer's field list is checked against the columns the ingest reads, and
a layer answering no rows raises: that is the portal changing shape, and it
must not freeze the city silently.  A transport failure degrades to the
epoch through `emit_freshness`.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, feature_count, field_max, layer_metadata
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://utility.arcgis.com/usrsvcs/servers/d8e68ae495d24f06a9adc233bf6e4c55/"
    "rest/services/AGO/Parks/MapServer/1",
    timeout=60,
)

# The columns stlouispark_tree_info.py reads.
REQUIRED_FIELDS = frozenset({"OBJECTID", "NAME", "GENUS", "SPECIES", "YRPLANT", "RETIRED"})


def check_layer() -> None:
    fields = {f.get("name") for f in layer_metadata(LAYER).get("fields") or []}
    missing = REQUIRED_FIELDS - fields
    if missing:
        raise RuntimeError(f"{LAYER.base} lacks {sorted(missing)} (has {sorted(fields)})")
    if feature_count(LAYER) <= 0:
        raise RuntimeError(f"{LAYER.base} answers no rows")


def fetch_modified_at() -> datetime:
    check_layer()
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = today - timedelta(days=today.weekday())
    retired = min(field_max(LAYER, "RETIRED"), now)
    return max(week, retired)


if __name__ == "__main__":
    emit_freshness("USSLP", fetch_modified_at)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Bristol's tree layer.

The layer is a MapServer layer on the council's own ArcGIS Server and
publishes no `editingInfo`, so the probe reads `MAX(last_edited_date)` with
`field_max`.  The council reloads the layer whole from its Confirm asset
system, and every row's `last_edited_date` is the moment of that load
(2026-11-16 when the city was wired), so this is "when the council last
republished", which is the only stamp the layer has.  If the load turns out
to run daily, the city rebuilds at each of its twice-weekly ticks; that is a
56k-row download, and there is no finer signal to watch.

`field_max` goes through `get_json_with_retry`, so an outage degrades to the
epoch; an empty statistics row or a renamed field raises.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://maps2.bristol.gov.uk/server2/rest/services/ext/"
    "ll_environment_and_planning/MapServer/32",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "last_edited_date")


if __name__ == "__main__":
    emit_freshness("GBBRS", fetch_modified_at)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Charlotte's tree inventory.

The layer (`ENG/LSM_Trees/MapServer/0` on the city's on-prem ArcGIS Server)
publishes no `editingInfo`, and its schema carries no date column at all, so
neither `layer_last_edit` nor `field_max` has anything to read.  This falls
back to the third watermark: the `modified` stamp the data.charlottenc.gov
Hub catalogue gives the dataset served at that endpoint, which read
2026-10-16T13:59:30Z when the city was wired.

See `hub_last_modified` for what that costs: it is the catalogue's stamp, not
the data's, and the city's Hub re-stamped many of its datasets within minutes
of each other that morning, so it moves with catalogue syncs as well as with
the trees.  Rebuilding on a sync costs one download; the twice-weekly cron and
the community and OSM probes bound the opposite failure, a service
overwritten in place without a new stamp.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import hub_last_modified
from shared.ingest import emit_freshness

HUB_HOST = "data.charlottenc.gov"
LAYER_URL = "https://gis.charlottenc.gov/arcgis/rest/services/ENG/LSM_Trees/MapServer/0"


def fetch_modified_at() -> datetime:
    return hub_last_modified(HUB_HOST, LAYER_URL)


if __name__ == "__main__":
    emit_freshness("USCLT", fetch_modified_at)

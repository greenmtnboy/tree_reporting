#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Antwerp's tree inventory.

The `boom` layer (portal_publiek6 MapServer 676, on the city's own ArcGIS
Server) publishes **no `editingInfo`** and has no date column at all, so
neither layer watermark is available; this reads the third, the Hub
catalogue's `modified` stamp for the dataset served at that layer, from
portaal-stadantwerpen.opendata.arcgis.com's DCAT feed (`hub_last_modified`).

When this was written the stamp moved daily (the city republishes its portal
services nightly: `boom` 2026-11-18T04:48Z, `toekomstboom` 2026-11-17T05:19Z),
so BEANR rebuilds on each scheduled tick.  That is ~135k rows over 68 small
pages, cheap enough, and there is no finer signal.  `hub_last_modified`
documents the other direction (a stamp that fails to move); the scheduled
cron bounds it.

A transport failure degrades to the epoch through `emit_freshness`; a feed
that no longer lists the layer raises, because that is our wiring being wrong.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness
from shared.platforms.arcgis import hub_last_modified

HUB_HOST = "portaal-stadantwerpen.opendata.arcgis.com"
LAYER_URL = (
    "https://geodata.antwerpen.be/arcgissql/rest/services/P_Portal/portal_publiek6/MapServer/676"
)


def fetch_modified_at() -> datetime:
    return hub_last_modified(HUB_HOST, LAYER_URL, timeout=180)


if __name__ == "__main__":
    emit_freshness("BEANR", fetch_modified_at)

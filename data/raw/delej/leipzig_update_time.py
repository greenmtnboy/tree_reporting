#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Leipzig's tree register.

Reads MAX(`fme_tstamp`) from the WFS layer (`OpenData:Baeume`): the date of
the FME load that regenerates the layer, the same on every row.  The layer has
no per-tree edit date, and the catalogue's `modified` (2024-07-15 on a layer
reloaded daily) does not move with the data, so the load date is the only
stamp the publisher keeps.  What that costs: the load runs every day whether
or not a tree changed, so the city rebuilds on every tick of its job --
twice a week, which a 187k-row WFS read (a couple of minutes) affords.

`fme_tstamp` is a bare date (Leipzig's calendar day), read as midnight UTC: a
date granularity, so the zone cannot reorder two loads.

A missing or unreadable `fme_tstamp` raises -- the layer changing shape -- and
a transport failure degrades to the epoch through `emit_freshness`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness  # noqa: E402
from shared.platforms.wfs import WfsLayer, wfs_max_property  # noqa: E402

LAYER = WfsLayer("https://geodienste.leipzig.de/l3/OpenData/wfs", "OpenData:Baeume", timeout=120)


def fetch_modified_at() -> datetime:
    return wfs_max_property(LAYER, "fme_tstamp")


if __name__ == "__main__":
    emit_freshness("DELEJ", fetch_modified_at)

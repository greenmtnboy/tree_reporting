#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Leipzig's green-space landmarks.

LICENCE: the publisher states no licence for this layer; it is reproduced for
display only (see leipzig_landmarks.py).

Reads MAX(`fme_tstamp`) from `OpenData:gruenflaechen_amt67`: the time of the
FME load that regenerates the layer (`2026-11-17T03:49:37.384+01:00`, with
its offset, so the zone is read as published).  The layer has no edit date
and no catalogue record, so the load time is the only stamp; it moves every
day, so the landmarks rebuild on every (weekly) tick of their job.

A missing or unreadable `fme_tstamp` raises -- the layer changing shape -- and
a transport failure degrades to the epoch through `emit_freshness`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness  # noqa: E402
from shared.platforms.wfs import WfsLayer, wfs_max_property  # noqa: E402

LAYER = WfsLayer(
    "https://geodienste.leipzig.de/l3/OpenData/wfs", "OpenData:gruenflaechen_amt67", timeout=120
)


def fetch_modified_at() -> datetime:
    return wfs_max_property(LAYER, "fme_tstamp")


if __name__ == "__main__":
    emit_freshness("DELEJ", fetch_modified_at, label="DELEJ landmarks")

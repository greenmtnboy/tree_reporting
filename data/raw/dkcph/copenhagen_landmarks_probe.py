#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Copenhagen's public monuments.

The `monumenter` layer carries no date column and the WFS publishes no
layer-level stamp, so there is nothing on the source to watch.  The watermark
is a constant, bumped by hand: the day the layer was last reviewed.  That is
the same contract the Burlington-pattern CSV cities have, where the watermark
is the committed file, and it is honest about what would trigger a rebuild --
a person looking at the layer again -- rather than pretending the register
reports a change it does not.

A live probe was tried and rejected: `resultType=hits` gives a row count,
which is not a time and would rebuild the city on a deletion as readily as an
addition while missing a rename entirely.  Bump `LAST_REVIEWED` after checking
the layer, and the weekly landmark lane republishes.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness

LAST_REVIEWED = datetime(2026, 9, 11, tzinfo=timezone.utc)


def fetch_modified_at() -> datetime:
    return LAST_REVIEWED


if __name__ == "__main__":
    emit_freshness("DKCPH", fetch_modified_at, label="DKCPH landmarks")

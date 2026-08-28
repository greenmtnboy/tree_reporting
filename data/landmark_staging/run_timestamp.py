#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy"]
# ///
"""Freshness probe for the ad-hoc landmark staging jobs: the current time.

A materialised target with no `freshness by` is rebuilt only while its object
does not exist — verified against a live staging object — so an ad-hoc
republish job needs a watermark that is always newer than the last build.
"Now" is that watermark, and it makes the *firing itself* the freshness
decision: `trilogy cloud jobs run urban-tree-landmarks-{code}` republishes the
committed CSV as the staging parquet, every time, which is the entire point
of a job whose trigger is a person.

That is also why these jobs must stay **unscheduled** (or on a rare cron at
most): on the main refresh's ticks this watermark would be the every-tick
thrash the staging design exists to prevent — republishing an unchanged CSV
touches the staging object's Last-Modified, which the city landmark probes
read, so the city's landmark parquet would rebuild for nothing.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "raw"))
from _ingest_shared import emit_freshness  # noqa: E402

if __name__ == "__main__":
    emit_freshness(
        None, lambda: datetime.now(timezone.utc), label="landmark staging run"
    )

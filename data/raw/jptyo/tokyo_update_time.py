#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Tokyo's metropolitan-road street trees.

Four small JSON reads against a 21 MB pair of CSVs, so a refresh only
re-downloads them when the Bureau of Construction has actually republished.

**The watermark is the later of the two resources**, because the ingest unions
both and either can move on its own: the 23-ward file was last published
2025-02-20 and the Tama file 2026-04-26, which is the survey campaign that
added the Tama half in the first place.  Taking one resource would freeze the
city whenever the *other* one was the thing that changed -- the silent
never-rebuilding failure a probe exists to prevent, and the same trap Toronto
fell into for a different reason (see the table in `_ckan_shared`).

`data_last_modified` raises rather than degrading when a resource carries no
stamp at all: that is the portal changing its metadata shape, not the portal
being down, and `emit_freshness` must not turn it into "no new data".  A
genuine outage is caught by `get_json_with_retry` inside it and does degrade to
the epoch, so Tokyo sits out that run and the other cities refresh normally.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ckan_shared import CkanResource, data_last_modified
from _ingest_shared import emit_freshness

# Both resources of package `t000014d2000000029`, "都道の街路樹".  Kept in step
# with `tokyo_tree_info.py` by `tests/test_city_wiring.py`, which checks that a
# city's probe and its ingest read the same source.
RESOURCES = (
    CkanResource("catalog.data.metro.tokyo.lg.jp", "8bdb63d0-911f-4e88-845f-14f6cab691a4"),
    CkanResource("catalog.data.metro.tokyo.lg.jp", "033acc60-dd24-402a-9f98-67e7fbdd1ec8"),
)


def fetch_modified_at() -> datetime:
    return max(data_last_modified(resource) for resource in RESOURCES)


if __name__ == "__main__":
    emit_freshness("JPTYO", fetch_modified_at)

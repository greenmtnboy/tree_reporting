#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Tokyo's designated cultural properties and historic sites.

The later of the two registers' publication times, matching the union
`tokyo_landmarks.py` builds: either register can be revised on its own, and
taking one would freeze the city's landmark parquet whenever the other was
what moved.

Neither resource carries a `last_modified` or a `created` stamp, so
`data_last_modified` falls back to the package's `metadata_modified` -- its
documented last resort, which moves for a description edit as well as for data.
That is the right trade here rather than a problem: the landmark lane runs
weekly, these registers change on a scale of years, and an occasional rebuild
of 287 rows costs nothing.  The alternative -- no watermark -- is what
`EXTENDING.md` says never to ship.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ckan_shared import CkanResource, data_last_modified
from _ingest_shared import emit_freshness

RESOURCES = (
    CkanResource("catalog.data.metro.tokyo.lg.jp", "25c5e6f7-0f8d-44d8-ac37-127e008f7a69"),
    CkanResource("catalog.data.metro.tokyo.lg.jp", "6fb22ee3-5138-4fee-b611-e041f2e47351"),
)


def fetch_modified_at() -> datetime:
    return max(data_last_modified(resource) for resource in RESOURCES)


if __name__ == "__main__":
    emit_freshness("JPTYO", fetch_modified_at, label="JPTYO landmarks")

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Milan's tree inventory: a constant.

milan_tree_info.py reads the Comune's 2023-03-31 release of DS2484 at a pinned
commit, because the portal stopped serving the dataset in its 2026 migration.
Pinned bytes never change, so there is nothing to watch: the watermark is the
release date.  When the Comune republishes DS2484, read its CKAN resource with
`shared.platforms.ckan.data_last_modified` here and point the ingest at it.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness

SNAPSHOT_RELEASED = datetime(2023, 3, 31, tzinfo=timezone.utc)


def fetch_modified_at() -> datetime:
    return SNAPSHOT_RELEASED


if __name__ == "__main__":
    emit_freshness("ITMIL", fetch_modified_at)

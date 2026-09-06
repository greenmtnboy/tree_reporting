#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Boston's BPRD tree inventory (CKAN).

Boston is the city this repo has read CKAN for the longest, and this probe used
to hand-roll `resource_show` and read `last_modified` with a `created`
fallback.  `_ckan_shared.data_last_modified` is that, plus the package's
`last_refreshed`, plus the reason to prefer the *maximum* of them rather than
the first one present -- which Toronto needs and Boston does not, since
Boston's datastore resource stamp does move.  One implementation, so the next
CKAN city inherits the rule instead of re-deriving it.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ckan_shared import CkanResource, data_last_modified
from _ingest_shared import emit_freshness

RESOURCE = CkanResource("data.boston.gov", "995cd80f-2489-41bf-b16b-113dba4f2797")


def fetch_rows_updated_at() -> datetime:
    return data_last_modified(RESOURCE)


if __name__ == "__main__":
    emit_freshness("USBOS", fetch_rows_updated_at)

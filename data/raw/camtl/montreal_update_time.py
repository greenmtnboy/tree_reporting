#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Montreal's public tree inventory.

Two small JSON reads against a 335,052-row dataset, so a refresh only
re-downloads the inventory when the city has actually republished it.

Watches the **consolidated** resource, the same one `montreal_tree_info.py`
reads.  The package's seven resources move independently -- the two borough
files last changed in 2020 and 2026 respectively -- so a probe pointed at the
package rather than at the resource we ingest would report freshness for a file
this city does not publish.

`data_last_modified` raises rather than degrading when no stamp is present:
that is the portal changing its metadata shape, not the portal being down, and
`emit_freshness` must not turn it into "no new data".  A genuine outage is
caught by `get_json_with_retry` inside it and does degrade to the epoch.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ckan_shared import CkanResource, data_last_modified
from _ingest_shared import emit_freshness

RESOURCE = CkanResource(
    "donnees.montreal.ca", "64e28fe6-ef37-437a-972d-d1d3f1f7d891"
)


def fetch_modified_at() -> datetime:
    return data_last_modified(RESOURCE)


if __name__ == "__main__":
    emit_freshness("CAMTL", fetch_modified_at)

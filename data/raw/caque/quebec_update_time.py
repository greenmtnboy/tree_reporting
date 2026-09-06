#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Quebec City's tree inventory.

Two small JSON reads against a 158,127-row dataset, so a refresh only
re-downloads the inventory when the city has actually republished it.

The host carries a path -- Donnees Quebec serves its CKAN API under
`/recherche` -- which `CkanResource` keeps; see `quebec_tree_info.py`.

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
    "www.donneesquebec.ca/recherche", "13a51853-a5b5-4add-8791-02ccba5c1be7"
)


def fetch_modified_at() -> datetime:
    return data_last_modified(RESOURCE)


if __name__ == "__main__":
    emit_freshness("CAQUE", fetch_modified_at)

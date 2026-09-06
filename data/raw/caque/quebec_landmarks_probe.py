#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for CAQUE's RPCQ heritage landmarks.

The register arrives as two resources (classified and cited -- see
`quebec_landmarks.py`), and this takes the **later** of their publication
times: either one moving means the city's landmark set may have changed, and
taking only one would freeze the lane on a republish of the other.

Both reads go through `_ckan_shared.data_last_modified`, which raises rather
than degrading when a portal has no usable stamp; a genuine outage is caught
inside it and degrades to the epoch, so the weekly landmark lane sits this city
out rather than failing every city.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ckan_shared import CkanResource, data_last_modified
from _ingest_shared import emit_freshness

HOST = "www.donneesquebec.ca/recherche"
CLASSIFIED = CkanResource(HOST, "c6c20af9-504f-4848-9ff2-32c463c9b04c")
CITED = CkanResource(HOST, "ba6bed2e-2b87-47fa-be28-681be1b4b649")


def fetch_modified_at() -> datetime:
    return max(data_last_modified(CLASSIFIED), data_last_modified(CITED))


if __name__ == "__main__":
    emit_freshness("CAQUE", fetch_modified_at, label="CAQUE landmarks")

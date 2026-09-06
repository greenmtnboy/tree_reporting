#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Toronto's street tree inventory.

Two small JSON reads against a 688,335-row dataset, so a refresh only
re-downloads the inventory when the city has actually republished it.

**Toronto is the reason `data_last_modified` takes a maximum rather than the
first stamp it finds.**  Its datastore is updated in place, so this resource's
own `last_modified` still reads 2022-05-02 while the data behind it was
refreshed in 2026 -- the package's non-standard `last_refreshed` is what moved.
Reading the resource stamp alone would have stored 2022 on the first build and
compared fresh for ever, which is the silent never-rebuilding failure a
freshness probe exists to prevent.  See the table in `_ckan_shared`.

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
    "ckan0.cf.opendata.inter.prod-toronto.ca",
    "3dafa392-c6ab-4f37-9bf9-21ddf7308eaf",
)


def fetch_modified_at() -> datetime:
    return data_last_modified(RESOURCE)


if __name__ == "__main__":
    emit_freshness("CATOR", fetch_modified_at)

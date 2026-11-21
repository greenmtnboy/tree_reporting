#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Greater Shepparton's tree inventory.

The inventory is the CSV resource of the data.gov.au dataset
`greater-shepparton-city-council-street-and-park-trees`; a re-upload moves
the resource's `last_modified` (2023-03-30 on 2026-11-21, matching the
file's HTTP Last-Modified).  `data_last_modified` reads that and the
package's `last_refreshed` (absent on data.gov.au) and takes the later, the
shared CKAN rule; the package's `metadata_modified` (2026-09-30, a catalogue
edit) is only a fallback, so a description edit does not rebuild the city.
Macedon Ranges' and Greater Bendigo's probe.

An outage degrades through `get_json_with_retry` (inside the CKAN module);
a payload with no timestamp at all raises, because that is data.gov.au
changing its metadata shape.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness  # noqa: E402
from shared.platforms.ckan import CkanResource, data_last_modified  # noqa: E402

RESOURCE = CkanResource("data.gov.au/data", "d56d39f9-1e15-48c4-bad1-0cbd8091ece6")


def fetch_modified_at() -> datetime:
    return data_last_modified(RESOURCE)


if __name__ == "__main__":
    emit_freshness("AUSHT", fetch_modified_at)

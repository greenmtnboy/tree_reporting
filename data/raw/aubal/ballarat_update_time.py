#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Ballarat's tree inventory.

One call to the OpenDataSoft catalogue record of the `trees` dataset on
data.ballarat.vic.gov.au, reading `.metas.default.modified` -- the Melbourne
probe's field.  The portal sets `modified_updates_on_data_change: true` and
`modified_updates_on_metadata_change: false` on this dataset, so the stamp
moves when the records change and not when someone edits the description.
The portal describes the dataset as "typically updated monthly on an
automated schedule"; it read 2026-11-15T15:42:30Z on 2026-11-16.

An outage (holding page, 5xx, reset connection) degrades through
`get_json_with_retry`; a payload without the field raises, because that is
the portal's schema moving, not the portal being down.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

METADATA_URL = (
    "https://data.ballarat.vic.gov.au/api/explore/v2.1/catalog/datasets/trees"
)


def fetch_modified_at() -> datetime:
    meta = get_json_with_retry(METADATA_URL, timeout=60)
    ts = ((meta.get("metas") or {}).get("default") or {}).get("modified")
    if not ts:
        raise RuntimeError("Ballarat trees metadata missing metas.default.modified")
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("AUBAL", fetch_modified_at)

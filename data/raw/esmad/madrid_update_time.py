#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Madrid's tree inventory on datos.madrid.es.

The package is republished twice a year by adding a new "Arbolado <year>"
release next to the old ones, so the watermark is the newest release's CSV
resource -- the same one the ingest reads (`latest_csv_resource`) -- and its
CKAN `last_modified` (falling back to `created`).  A new year's resource, or a
re-upload of the current one, moves it.

An outage degrades through `emit_freshness`; a package with no recognisable
release, or a resource with no parseable stamp, raises.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
from shared.ingest import emit_freshness  # noqa: E402
from madrid_tree_info import latest_csv_resource  # noqa: E402


def fetch_modified_at() -> datetime:
    resource = latest_csv_resource()
    stamps = []
    for key in ("last_modified", "created"):
        value = resource.get(key)
        if value:
            stamp = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
            stamps.append(stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc))
    if not stamps:
        raise RuntimeError(
            f"Madrid: resource {resource.get('id')} carries neither last_modified nor created"
        )
    return max(stamps)


if __name__ == "__main__":
    emit_freshness("ESMAD", fetch_modified_at)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

"""
Freshness probe for the London Public Realm Trees dataset.

Uses the London Datastore CKAN package_show API to retrieve the dataset's
metadata_modified timestamp — a lightweight metadata call vs. downloading
the full 200 MB CSV.

Package ID: 2r45m
API: GET https://data.london.gov.uk/api/3/action/package_show?id=2r45m
Reads: .result.metadata_modified  (ISO 8601 string)
"""

import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness, get_json_with_retry

PACKAGE_URL = "https://data.london.gov.uk/api/3/action/package_show?id=2r45m"


def fetch_modified_at() -> datetime:
    data = get_json_with_retry(PACKAGE_URL)
    result = data.get("result", {})
    ts = result.get("metadata_modified") or result.get("metadata_created")
    if ts is None:
        raise RuntimeError("CKAN package metadata missing metadata_modified field")
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("GBLON", fetch_modified_at)

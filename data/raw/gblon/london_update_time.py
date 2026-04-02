#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests"]
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
import requests
import pyarrow as pa
from datetime import datetime, timezone

PACKAGE_URL = "https://data.london.gov.uk/api/3/action/package_show?id=2r45m"


def fetch_modified_at() -> datetime:
    r = requests.get(PACKAGE_URL, timeout=30)
    r.raise_for_status()
    data = r.json()
    result = data.get("result", {})
    ts = result.get("metadata_modified") or result.get("metadata_created")
    if ts is None:
        raise RuntimeError("CKAN package metadata missing metadata_modified field")
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def emit(updated_at: datetime) -> None:
    table = pa.table(
        {
            "city": pa.array(["GBLON"], type=pa.string()),
            "data_updated_through": pa.array(
                [updated_at], type=pa.timestamp("us", tz="UTC")
            ),
        }
    )
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == "__main__":
    emit(fetch_modified_at())

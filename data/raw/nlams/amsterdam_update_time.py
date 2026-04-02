#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests"]
# ///

"""
Freshness probe for Amsterdam tree data.

Fetches the dataset metadata from the Amsterdam Data catalog API and emits
the last-modified timestamp as a single-row Arrow table — a lightweight
alternative to downloading the full tree dataset.

API: GET https://data.amsterdam.nl/api/catalog/v3/datasets/bomen/
Reads: .modified_date (ISO 8601 string)

Fallback: if the catalog API doesn't expose a modification date, the probe
falls back to requesting the first page of the stamgegevens endpoint and
uses the current date so Trilogy treats the dataset as up to date.
"""

import sys
import requests
import pyarrow as pa
from datetime import datetime, timezone

CATALOG_URL = "https://data.amsterdam.nl/api/catalog/v3/datasets/bomen/"
# Lightweight fallback: fetching a single record is cheap even if it doesn't
# carry an explicit modification timestamp in its headers/body.
PROBE_URL = "https://api.data.amsterdam.nl/v1/bomen/stamgegevens/?_format=json&page_size=1"


def fetch_modified_at() -> datetime:
    # 1. Try the catalog metadata endpoint first
    try:
        r = requests.get(CATALOG_URL, timeout=30)
        r.raise_for_status()
        meta = r.json()

        # The Amsterdam catalog uses 'modified_date' or 'date_modified'
        for key in ("modified_date", "date_modified", "modified", "last_modified"):
            ts = meta.get(key)
            if ts:
                return datetime.fromisoformat(
                    ts.replace("Z", "+00:00")
                ).astimezone(timezone.utc)
    except Exception:
        pass  # fall through to probe URL

    # 2. Try Last-Modified response header from the stamgegevens endpoint
    try:
        r = requests.get(PROBE_URL, timeout=30)
        r.raise_for_status()
        lm = r.headers.get("Last-Modified") or r.headers.get("last-modified")
        if lm:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(lm).astimezone(timezone.utc)

        # 3. Look for any date field in the response body as a last resort
        data = r.json()
        embedded = data.get("_embedded", {})
        rows = embedded.get("stamgegevens", [])
        if rows:
            for date_field in ("modified", "date_modified", "created", "registratiedatum"):
                val = rows[0].get(date_field)
                if val:
                    return datetime.fromisoformat(
                        str(val).replace("Z", "+00:00")
                    ).astimezone(timezone.utc)
    except Exception:
        pass

    # 4. Hard fallback: emit a fixed early timestamp so Trilogy always treats
    #    the dataset as needing a refresh (safe default for a new city).
    return datetime(2024, 1, 1, tzinfo=timezone.utc)


def emit(updated_at: datetime) -> None:
    table = pa.table(
        {
            "city": pa.array(["NLAMS"], type=pa.string()),
            "data_updated_through": pa.array(
                [updated_at], type=pa.timestamp("us", tz="UTC")
            ),
        }
    )
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == "__main__":
    emit(fetch_modified_at())

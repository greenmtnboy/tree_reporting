#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

"""
Freshness probe for Amsterdam landmarks (gemeentelijke monumenten).

Fetches the dataset metadata from the Amsterdam Data catalog API and emits
the last-modified timestamp as a single-row Arrow table.

Primary:  GET https://data.amsterdam.nl/api/catalog/v3/datasets/monumenten/
Fallback: Last-Modified response header from the monumenten API endpoint.
"""

from datetime import datetime, timezone
from pathlib import Path

import requests

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness

CATALOG_URL = "https://data.amsterdam.nl/api/catalog/v3/datasets/monumenten/"
PROBE_URL = "https://api.data.amsterdam.nl/v1/monumenten/monumenten/?_format=json&page_size=1"


def fetch_last_modified() -> datetime:
    # 1. Try the catalog metadata endpoint
    try:
        r = requests.get(CATALOG_URL, timeout=30)
        r.raise_for_status()
        meta = r.json()
        for key in ("modified_date", "date_modified", "modified", "last_modified"):
            ts = meta.get(key)
            if ts:
                return datetime.fromisoformat(
                    str(ts).replace("Z", "+00:00")
                ).astimezone(timezone.utc)
    except Exception:
        pass

    # 2. Try Last-Modified header from the monumenten endpoint
    try:
        r = requests.get(PROBE_URL, timeout=30)
        r.raise_for_status()
        lm = r.headers.get("Last-Modified") or r.headers.get("last-modified")
        if lm:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(lm).astimezone(timezone.utc)
    except Exception:
        pass

    # 3. Hard fallback
    return datetime(2024, 1, 1, tzinfo=timezone.utc)


if __name__ == "__main__":
    emit_freshness("NLAMS", fetch_last_modified)

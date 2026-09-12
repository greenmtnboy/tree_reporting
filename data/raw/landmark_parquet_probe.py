#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for the landmark union: when any city last published.

`raw/full_landmark_publish.preql` builds `full_landmark_info` from the published
per-city landmark parquets, and this is what tells it whether any of them has
moved.  It HEADs each object and emits the newest `Last-Modified`.

The tree rollup's probe next door, `city_parquet_probe.py`, is the same idea and
carries the full argument for taking the watermark from the *objects* rather
than from a column inside them.  It applies here for an extra reason: each
city's landmark parquet names its watermark after itself
(`ussfo_landmark_data_updated_through`, ...), so a union that read the column
would need forty-one stub datasources to map forty-one names onto one concept --
which is exactly what `landmark_info.preql` was doing by importing every city
model, and what took the whole union down whenever one city's portal was unwell.

A city whose landmark parquet does not exist yet contributes nothing rather than
failing: absence is not staleness, and the union should keep publishing the
cities that do have one.  A transport failure raises `UpstreamUnavailable`, so
`emit_freshness` degrades the probe to the epoch and the union sits the tick out
rather than republishing from a bucket we cannot currently read.
"""

import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from _ingest_shared import (  # noqa: E402
    CITY_BOUNDS,
    UpstreamUnavailable,
    emit_freshness,
)

LANDMARKS_BASE_URL = (
    "https://storage.googleapis.com/trilogy_public_models/duckdb/landmarks"
)


def landmark_parquet_url(city_code: str, data_version: str = "2") -> str:
    """Public read URL for one city's published landmark parquet."""
    return f"{LANDMARKS_BASE_URL}/{city_code.lower()}_landmark_info_v{data_version}.parquet"


def newest_publication() -> datetime:
    # Keyed on CITY_BOUNDS rather than MUNICIPAL_DATA_SOURCES: every city on the
    # map has landmarks, including the community-only ones whose municipal tuple
    # is empty (Milos, Santorini).
    newest = datetime.fromtimestamp(0, tz=timezone.utc)
    missing: list[str] = []
    for code in CITY_BOUNDS:
        url = f"{landmark_parquet_url(code)}?cb={int(time.time())}"
        try:
            response = requests.head(url, timeout=30, allow_redirects=True)
        except requests.RequestException as err:
            raise UpstreamUnavailable(
                f"Failed to HEAD {code}'s published landmark parquet: {err}"
            ) from err
        if response.status_code == 404:
            missing.append(code)
            continue
        if response.status_code >= 400:
            raise UpstreamUnavailable(f"HEAD {url} returned HTTP {response.status_code}")
        header = response.headers.get("Last-Modified")
        if not header:
            # Not availability: GCS always sends this, so its absence means the
            # URL is not the object we think it is.
            raise RuntimeError(f"{code}'s published landmark parquet has no Last-Modified")
        newest = max(newest, parsedate_to_datetime(header).astimezone(timezone.utc))
    if missing:
        print(
            f"no published landmark parquet yet for {', '.join(missing)}; the union "
            "will be built from the cities that have one",
            file=sys.stderr,
        )
    return newest


if __name__ == "__main__":
    emit_freshness(None, newest_publication, label="landmark parquet publication")

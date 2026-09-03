#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for the cross-city rollup: when any city last published.

`raw/full_tree_publish.preql` builds `full_tree_info` from the seventeen
published city parquets, and this is what tells it whether any of them has
moved.  It HEADs each object and emits the newest `Last-Modified`.

**Object publication time, not a column read out of the files.**  The rollup
used to carry a per-city `{code}_published_data_updated_through` column and
take `max()` over the union, which meant seventeen near-identical stub
datasources existed largely to map seventeen differently-named columns onto one
concept.  Reading the objects' metadata instead is both smaller and a better
signal: a city rebuild that does *not* advance its data watermark -- adding a
column, a re-run after a model fix -- still republishes the object, and the
rollup now notices.  Under the column watermark it did not, which is the
"a new column does not make a Parquet stale" trap documented in EXTENDING.md.

Seventeen HEAD requests, no bodies; the objects are served with
`Cache-Control: max-age=3600` so each carries a cache-buster, exactly as
`staging_modified_at` does -- without one a probe run just after a city
published would read the previous time and call the rollup fresh.

A city whose parquet does not exist yet (a brand-new city, first build pending)
contributes nothing rather than failing: absence is not staleness, and the
rollup should keep publishing the sixteen that do exist.  A transport failure
raises `UpstreamUnavailable`, so `emit_freshness` degrades the whole probe to
the epoch and the rollup sits the tick out rather than rebuilding from a bucket
we cannot currently read.
"""

import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from _ingest_shared import (  # noqa: E402
    MUNICIPAL_DATA_SOURCES,
    UpstreamUnavailable,
    emit_freshness,
)

TREES_BASE_URL = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees"


def city_parquet_url(city_code: str, data_version: str = "2") -> str:
    """Public read URL for one city's published tree parquet."""
    return f"{TREES_BASE_URL}/{city_code.lower()}_tree_info_v{data_version}.parquet"


def newest_publication() -> datetime:
    newest = datetime.fromtimestamp(0, tz=timezone.utc)
    missing: list[str] = []
    for code in MUNICIPAL_DATA_SOURCES:
        url = f"{city_parquet_url(code)}?cb={int(time.time())}"
        try:
            response = requests.head(url, timeout=30, allow_redirects=True)
        except requests.RequestException as err:
            raise UpstreamUnavailable(
                f"Failed to HEAD {code}'s published parquet: {err}"
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
            raise RuntimeError(f"{code}'s published parquet has no Last-Modified")
        newest = max(newest, parsedate_to_datetime(header).astimezone(timezone.utc))
    if missing:
        print(
            f"no published parquet yet for {', '.join(missing)}; the rollup will "
            "be built from the cities that have one",
            file=sys.stderr,
        )
    return newest


if __name__ == "__main__":
    emit_freshness(None, newest_publication, label="city parquet publication")

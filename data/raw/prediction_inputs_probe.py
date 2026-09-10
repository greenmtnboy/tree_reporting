#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for tree_predictions: when either of its inputs last published.

`raw/tree_predictions.preql` derives every row from two published objects --
the cross-city rollup and the species enrichment table -- and this emits the
newer of their `Last-Modified` times, so the predictions rebuild when a city
lands in the rollup or a species gains a growth form, and not otherwise.

Object publication time rather than a column inside the objects, for the
reason `city_parquet_probe.py` gives: a rebuild that changes the shape and not
the watermark still republishes the object, and this notices.

Both HEADs carry a cache-buster (the objects are served with
`Cache-Control: max-age=3600`). A missing object is not staleness -- the
predictions cannot be built without either input, so a 404 degrades to the
epoch through `UpstreamUnavailable` and the job sits the tick out, as does a
transport failure.
"""

import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from _ingest_shared import UpstreamUnavailable, emit_freshness  # noqa: E402
from enrichment._tree_shared import DATA_VERSION  # noqa: E402

TREES_BASE_URL = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees"

INPUTS = {
    "rollup": f"{TREES_BASE_URL}/full_tree_info_v{DATA_VERSION}.parquet",
    "enrichment": f"{TREES_BASE_URL}/tree_enrichment_v{DATA_VERSION}.parquet",
}


def published_at(label: str, url: str) -> datetime:
    try:
        response = requests.head(f"{url}?cb={int(time.time())}", timeout=30, allow_redirects=True)
    except requests.RequestException as err:
        raise UpstreamUnavailable(f"Failed to HEAD the {label} parquet: {err}") from err
    if response.status_code >= 400:
        raise UpstreamUnavailable(f"HEAD {url} returned HTTP {response.status_code}")
    header = response.headers.get("Last-Modified")
    if not header:
        # Not availability: GCS always sends this, so its absence means the
        # URL is not the object we think it is.
        raise RuntimeError(f"the {label} parquet has no Last-Modified")
    return parsedate_to_datetime(header).astimezone(timezone.utc)


def newest_input_publication() -> datetime:
    return max(published_at(label, url) for label, url in INPUTS.items())


if __name__ == "__main__":
    emit_freshness(None, newest_input_publication, label="prediction inputs")

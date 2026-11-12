#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy>=0.3.391", "requests"]
# ///
"""Freshness probe for the allometry fits: the newer of their inputs' publication, by week.

The fits (allometry_fits.preql, height_fits.preql) are made on the cross-city
rollup and the species enrichment table, and this emits the newer of their
`Last-Modified` times floored to the Monday (UTC) of its week. So the fits
refit at most once a week, on the first core tick of a week in which either
input moved, and not on every day some city publishes: a coefficient made on
ten million trees does not move with one city's daily update, and every city's
predictions and map projection rebuild when the fit tables republish
(shared/city_derived.py).

Object publication time rather than a column inside the objects, for the
reason `city_parquet_probe.py` gives. Both HEADs carry a cache-buster. A
missing object is not staleness -- the fits cannot be built without either
input, so a 404 degrades to the epoch through `UpstreamUnavailable` and the
jobs sit the tick out, as does a transport failure.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from shared.city_derived import published_at  # noqa: E402
from shared.ingest import emit_freshness  # noqa: E402
from enrichment._tree_shared import DATA_VERSION  # noqa: E402

TREES_BASE_URL = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees"

INPUTS = {
    "rollup": f"{TREES_BASE_URL}/full_tree_info_v{DATA_VERSION}.parquet",
    "enrichment": f"{TREES_BASE_URL}/tree_enrichment_v{DATA_VERSION}.parquet",
}


def week_of(moment: datetime) -> datetime:
    """Midnight UTC on the Monday of the moment's week."""
    day = moment.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return day - timedelta(days=day.weekday())


def newest_input_publication() -> datetime:
    return week_of(max(published_at(label, url) for label, url in INPUTS.items()))


if __name__ == "__main__":
    emit_freshness(None, newest_input_publication, label="fit inputs")

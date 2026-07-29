#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

"""Emit the newest approved-tree timestamp per city for Trilogy freshness.

Per city, not one global timestamp: this feeds every city's
`{code}_published_data_updated_through`, so a single scalar would make an
approval in one city mark all fourteen city Parquets stale and re-materialize
every one of them — a full re-download of every municipal dataset to publish one
tree.

The per-city values are emitted as one row of *columns*
(`ussfo_community_data_updated_through`, …), not as one row per city.  A row per
city does not isolate anything: Trilogy pushes a datasource's `complete where`
clause into row queries but not into the watermark probe, which stays a plain
`SELECT MAX(col) FROM uv_run(...)` over every row the script emits.  Verified —
with a row-per-city layout, a Boston-only approval moved San Francisco's
watermark too.  One column per city gives each city's probe its own scalar, and
mirrors how tree_common.preql already models the municipal probes.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import requests

sys.path.insert(0, str(Path(__file__).parent))
from _ingest_shared import COMMUNITY_DATA_SOURCES  # noqa: E402
from community_tree_info import PUBLISHED_BUCKET  # noqa: E402

# A city with no approvals still needs a value; the epoch reads as "no community
# data yet" and never wins the `greatest()` against a real municipal timestamp.
EMPTY_DATASET_TIMESTAMP = datetime(1970, 1, 1, tzinfo=timezone.utc)

MANIFEST_URL = os.environ.get(
    "COMMUNITY_PUBLISHED_MANIFEST_URL",
    f"https://storage.googleapis.com/{PUBLISHED_BUCKET}/community/manifest.json",
)


def column_for(city_code: str) -> str:
    """Column (and Trilogy property) holding *city_code*'s community timestamp."""
    return f"{city_code.lower()}_community_data_updated_through"


def fetch_published_at_by_city() -> dict[str, datetime]:
    """Newest `publishedAt` per city code, defaulting every city to the epoch.

    A missing or unreadable manifest means nothing has been approved yet.
    Returning epochs rather than raising matters: every city's
    `*_published_data_updated_through` depends on this probe, so an exception
    here would abort the entire `trilogy refresh raw` run — all cities, plus
    landmarks and enrichment — over an optional data source.
    """
    by_city = {code: EMPTY_DATASET_TIMESTAMP for code in COMMUNITY_DATA_SOURCES}
    try:
        response = requests.get(MANIFEST_URL, timeout=30)
        if response.status_code == 404:
            return by_city
        response.raise_for_status()
        published = response.json().get("latestPublishedAtByCity") or {}
    except (requests.RequestException, ValueError) as e:
        print(
            f"Community freshness probe: {MANIFEST_URL} unavailable ({e}); "
            "treating community data as empty",
            file=sys.stderr,
        )
        return by_city

    for city, value in published.items():
        code = str(city).upper().strip()
        if code not in by_city or not value:
            continue
        by_city[code] = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    return by_city


def emit_timestamps(by_city: dict[str, datetime]) -> None:
    table = pa.table(
        {
            column_for(code): pa.array([by_city[code]], type=pa.timestamp("us", tz="UTC"))
            for code in sorted(by_city)
        }
    )
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == "__main__":
    emit_timestamps(fetch_published_at_by_city())

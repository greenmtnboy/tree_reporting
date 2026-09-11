#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

"""Emit the newest published satellite-tree timestamp per city for freshness.

One *column* per wired city (`ussfo_satellite_data_updated_through`, ...),
for the reason community_update_time.py gives: Trilogy's watermark probe is a
plain `SELECT MAX(col)` over every row the script emits, so a row-per-city
layout would let a publish in one city mark every other city stale.  Each
city's model probes only its own column.

The manifest the reviewer writes on publish (`satellite/manifest.json`)
carries `latestPublishedAtByCity`; a city with nothing published, or an
unreachable manifest, reads as the epoch, which never wins the `greatest()`
against a real municipal timestamp.  Raising here would abort every city's
refresh over an optional source.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import requests

sys.path.insert(0, str(Path(__file__).parent))
from _ingest_shared import SATELLITE_DATA_SOURCES  # noqa: E402
from satellite_tree_info import PUBLISHED_BUCKET  # noqa: E402

EMPTY_DATASET_TIMESTAMP = datetime(1970, 1, 1, tzinfo=timezone.utc)

MANIFEST_URL = os.environ.get(
    "SATELLITE_PUBLISHED_MANIFEST_URL",
    f"https://storage.googleapis.com/{PUBLISHED_BUCKET}/satellite/manifest.json",
)


def column_for(city_code: str) -> str:
    """Column (and Trilogy property) holding *city_code*'s satellite timestamp."""
    return f"{city_code.lower()}_satellite_data_updated_through"


def fetch_published_at_by_city() -> dict[str, datetime]:
    by_city = {code: EMPTY_DATASET_TIMESTAMP for code in SATELLITE_DATA_SOURCES}
    try:
        response = requests.get(MANIFEST_URL, timeout=30)
        if response.status_code == 404:
            return by_city
        response.raise_for_status()
        published = response.json().get("latestPublishedAtByCity") or {}
    except (requests.RequestException, ValueError) as e:
        print(
            f"Satellite freshness probe: {MANIFEST_URL} unavailable ({e}); "
            "treating satellite data as empty",
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

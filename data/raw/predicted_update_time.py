#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

"""Emit each wired city's staged-predictions publication time, for freshness.

One *column* per city in PREDICTED_DATA_SOURCES
(`usbos_predicted_data_updated_through`, ...), for the reason
satellite_update_time.py gives: the watermark probe is a plain MAX over every
row, so a single column shared by cities would let one city's upload mark the
others stale.  The value is the staged object's GCS Last-Modified
(`staging_modified_at`), so a city rebuilds when a new prediction run is
uploaded and at no other time; a missing object is the epoch.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent))
from shared.ingest import (  # noqa: E402
    PREDICTED_DATA_SOURCES,
    UpstreamUnavailable,
    emit,
    staging_modified_at,
)
from predicted_tree_info import staging_name  # noqa: E402

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def column_for(city_code: str) -> str:
    """Column (and Trilogy property) holding *city_code*'s predictions timestamp."""
    return f"{city_code.lower()}_predicted_data_updated_through"


def fetch_modified_by_city() -> dict[str, datetime]:
    by_city: dict[str, datetime] = {}
    for code in PREDICTED_DATA_SOURCES:
        try:
            by_city[code] = staging_modified_at(staging_name(code))
        except UpstreamUnavailable as e:
            # An optional source must never abort the refresh: sit this one out.
            print(f"Predicted freshness probe: {code} unavailable ({e}); treating as empty", file=sys.stderr)
            by_city[code] = EPOCH
    return by_city


def main() -> None:
    by_city = fetch_modified_by_city()
    emit(
        pa.table(
            {column_for(code): pa.array([at], type=pa.timestamp("us", tz="UTC")) for code, at in sorted(by_city.items())}
        )
    )


if __name__ == "__main__":
    main()

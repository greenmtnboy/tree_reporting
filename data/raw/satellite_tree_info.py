#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

"""Emit reviewed aerial-imagery detections in the canonical tree schema.

The rows are model detections on NAIP tiles that a person accepted in the
reviewer's satellite page (`reviewer/satellite.ts`) and then published.  As
with community submissions, the source of truth is the *public export* the
reviewer writes on publish -- `satellite/published_trees.ndjson` in the
published bucket -- and never Firestore, which the pipeline cannot read
without credentials (see community_tree_info.py for the 403 story).  A
missing export is zero rows, not an error.

What a row carries, and what it deliberately does not:

* `species` is what the reviewer confirmed: the model's label if they
  accepted it, a name they typed, or nothing.  A model label a person did not
  look at never reaches this column -- the reviewer publishes `species` only
  from an accepted observation, and `speciesSource` says which.
* `diameter_at_breast_height` is filled from `measuredDbhInches` only.  The
  model's `predictedDbhInches` is an estimate, and the canonical column means
  a measurement everywhere else in the map; publishing the estimate there
  would let it win a cluster merge over a null municipal value and be shown
  as measured.  The estimate stays in the export for the quality join.
* Position: the detection is a crown centre, which is not where the trunk
  is -- tall trees lean away from nadir.  A detection the reviewer linked to
  an existing tree (`duplicateOfTreeId`) is exported *at that tree's
  coordinates*, so the shared cluster merge (tree_dedup.preql), whose
  matching is a grid equi-join, is guaranteed to put the two in one cluster;
  the municipal position then wins the merge and the satellite row only
  fills attributes the inventory left empty.  `positionRole` records which.

The row's `data_source` is `SATELLITE_{code}`, a fourth partition beside the
municipal, community and OSM ones; the shared merge classes it below
municipal and community and above OSM.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

import pyarrow as pa
import requests

sys.path.insert(0, str(Path(__file__).parent))
from _ingest_shared import (  # noqa: E402
    SATELLITE_DATA_SOURCES,
    emit,
    enforce_tree_schema,
    in_city_territory,
    normalize_species,
    parse_pushdown_filters,
)

PUBLISHED_BUCKET = os.environ.get(
    "COMMUNITY_PUBLISHED_BUCKET", "sf-tree-reporting-published"
)
PUBLISHED_EXPORT_URL = os.environ.get(
    "SATELLITE_PUBLISHED_EXPORT_URL",
    f"https://storage.googleapis.com/{PUBLISHED_BUCKET}/satellite/published_trees.ndjson",
)

# The export's own contract; a record with another version is skipped with a
# message rather than mis-read.
EXPORT_SCHEMA_VERSION = 1


def load_published_records() -> Iterable[Mapping[str, Any]]:
    """Read the reviewer's public satellite export."""
    response = requests.get(PUBLISHED_EXPORT_URL, timeout=60)
    if response.status_code == 404:
        print(
            f"Satellite ingest: no export at {PUBLISHED_EXPORT_URL}; emitting 0 rows",
            file=sys.stderr,
        )
        return []
    response.raise_for_status()
    records = []
    for line in response.text.splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def _positive_float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def records_to_table(records: Iterable[Mapping[str, Any]]) -> pa.Table:
    rows: list[dict[str, Any]] = []
    for record in records:
        tree_id = str(record.get("treeId") or "").strip()
        version = record.get("schemaVersion", EXPORT_SCHEMA_VERSION)
        if version != EXPORT_SCHEMA_VERSION:
            print(
                f"Satellite ingest: skipping {tree_id or '<unknown>'}; "
                f"export schema version {version!r} is not {EXPORT_SCHEMA_VERSION}",
                file=sys.stderr,
            )
            continue
        city = str(record.get("city") or "").upper().strip()
        data_source = SATELLITE_DATA_SOURCES.get(city)
        if not tree_id or data_source is None:
            print(
                f"Satellite ingest: skipping {tree_id or '<unknown>'}; "
                f"{city!r} has no satellite partition",
                file=sys.stderr,
            )
            continue
        try:
            latitude = float(record["latitude"])
            longitude = float(record["longitude"])
        except (KeyError, TypeError, ValueError):
            print(
                f"Satellite ingest: skipping {tree_id}; invalid coordinates",
                file=sys.stderr,
            )
            continue
        if not in_city_territory(city, latitude, longitude):
            print(
                f"Satellite ingest: skipping {tree_id}; coordinates outside {city}",
                file=sys.stderr,
            )
            continue

        rows.append(
            {
                "tree_id": tree_id,
                "city": city,
                "data_source": data_source,
                # Unconfirmed stays unidentified; enforce_tree_schema applies
                # the shared Unknown sentinel.
                "species": normalize_species(record.get("species")),
                "tree_name": None,
                "plant_date": None,
                # Never the model's estimate -- see the module docstring.
                "diameter_at_breast_height": _positive_float_or_none(
                    record.get("measuredDbhInches")
                ),
                "latitude": latitude,
                "longitude": longitude,
                "submission_photo_url": None,
                # City-specific extra; see gblon/london_tree_info.preql.
                "borough": None,
            }
        )

    schema = pa.schema(
        [
            ("tree_id", pa.string()),
            ("city", pa.string()),
            ("data_source", pa.string()),
            ("species", pa.string()),
            ("tree_name", pa.string()),
            ("plant_date", pa.date32()),
            ("diameter_at_breast_height", pa.float64()),
            ("latitude", pa.float64()),
            ("longitude", pa.float64()),
            ("submission_photo_url", pa.string()),
            ("borough", pa.string()),
        ]
    )
    table = pa.Table.from_pylist(rows, schema=schema)
    return enforce_tree_schema(table, city="Satellite")


def main(argv: list[str] | None = None) -> None:
    filters = parse_pushdown_filters(list(sys.argv[1:] if argv is None else argv))
    records = load_published_records()
    city = filters.get("city")
    if city:
        wanted = city.upper()
        records = [
            r for r in records
            if str(r.get("city") or "").upper().strip() == wanted
        ]
    emit(records_to_table(records))


if __name__ == "__main__":
    main()

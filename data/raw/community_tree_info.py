#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

"""Emit approved community trees in the canonical tree schema.

Source of truth is the public export the reviewer writes on approval — NOT
Firestore directly.  `firestore.googleapis.com/v1` is the Cloud REST API and
enforces IAM, not Firebase security rules, so a `allow read: if true` rule does
not make the collection readable to an unauthenticated pipeline; it returns 403
PERMISSION_DENIED.  Reading a public GCS object instead keeps Firestore private
and keeps this ingest credential-free.

The export is newline-delimited JSON, one approved tree per line, written by
`reviewer/server.ts` on approve.  A missing export (nobody has approved
anything yet) is not an error — it yields zero rows.
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
from _ingest_shared import (
    CITY_BOUNDS,
    COMMUNITY_DATA_SOURCES,
    emit,
    enforce_tree_schema,
    normalize_species,
)

PUBLISHED_BUCKET = os.environ.get(
    "COMMUNITY_PUBLISHED_BUCKET", "sf-tree-reporting-published"
)
PUBLISHED_EXPORT_URL = os.environ.get(
    "COMMUNITY_PUBLISHED_EXPORT_URL",
    f"https://storage.googleapis.com/{PUBLISHED_BUCKET}/community/published_trees.ndjson",
)


def load_published_records() -> Iterable[Mapping[str, Any]]:
    """Read the reviewer's public approved-tree export."""
    response = requests.get(PUBLISHED_EXPORT_URL, timeout=60)
    if response.status_code == 404:
        print(
            f"Community ingest: no export at {PUBLISHED_EXPORT_URL}; emitting 0 rows",
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


def records_to_table(records: Iterable[Mapping[str, Any]]) -> pa.Table:
    rows: list[dict[str, Any]] = []
    for record in records:
        tree_id = str(record.get("treeId") or "").strip()
        city = str(record.get("city") or "").upper().strip()
        # `species` is a Trilogy key and must remain non-null even though the
        # submission form allows an unidentified tree.
        species = normalize_species(record.get("species")) or "Unknown"
        try:
            latitude = float(record["latitude"])
            longitude = float(record["longitude"])
        except (KeyError, TypeError, ValueError):
            print(
                f"Community ingest: skipping {tree_id or '<unknown>'}; invalid coordinates",
                file=sys.stderr,
            )
            continue

        bounds = CITY_BOUNDS.get(city)
        data_source = COMMUNITY_DATA_SOURCES.get(city)
        if not tree_id or bounds is None or data_source is None:
            print(
                f"Community ingest: skipping {tree_id or '<unknown>'}; unsupported city {city!r}",
                file=sys.stderr,
            )
            continue
        lat_min, lat_max, lon_min, lon_max = bounds
        if not (lat_min <= latitude <= lat_max and lon_min <= longitude <= lon_max):
            print(
                f"Community ingest: skipping {tree_id}; coordinates outside {city}",
                file=sys.stderr,
            )
            continue

        photo_url = record.get("photoUrl")
        rows.append(
            {
                "tree_id": tree_id,
                "city": city,
                "data_source": data_source,
                "species": species,
                "tree_name": species,
                "plant_date": None,
                "diameter_at_breast_height": None,
                "latitude": latitude,
                "longitude": longitude,
                "submission_photo_url": str(photo_url) if photo_url else None,
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
    # data_source is already per-row here (one value per city), so it is not
    # passed as a constant.
    return enforce_tree_schema(table, city="Community")


if __name__ == "__main__":
    emit(records_to_table(load_published_records()))

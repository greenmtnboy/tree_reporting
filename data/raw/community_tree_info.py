#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

"""Emit approved community trees from Firestore in the canonical tree schema."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import pyarrow as pa
import requests

sys.path.insert(0, str(Path(__file__).parent))
from _ingest_shared import CITY_BOUNDS, emit, enforce_tree_schema, normalize_species

FIREBASE_PROJECT_ID = os.environ.get(
    "FIREBASE_PROJECT_ID", "sf-tree-reporting-prod"
)


def _decode_firestore_value(value: Mapping[str, Any]) -> Any:
    if "nullValue" in value:
        return None
    if "stringValue" in value:
        return value["stringValue"]
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "integerValue" in value:
        return int(value["integerValue"])
    if "timestampValue" in value:
        return datetime.fromisoformat(str(value["timestampValue"]).replace("Z", "+00:00"))
    raise ValueError(f"Unsupported Firestore value: {value}")


def load_published_records() -> Iterable[Mapping[str, Any]]:
    """Read the rules-public published collection without privileged credentials."""
    url = (
        "https://firestore.googleapis.com/v1/projects/"
        f"{FIREBASE_PROJECT_ID}/databases/(default)/documents/publishedTrees"
    )
    params: dict[str, str | int] = {"pageSize": 1000}
    while True:
        response = requests.get(url, params=params, timeout=60)
        response.raise_for_status()
        payload = response.json()
        for document in payload.get("documents", []):
            yield {
                key: _decode_firestore_value(value)
                for key, value in document.get("fields", {}).items()
            }
        token = payload.get("nextPageToken")
        if not token:
            return
        params["pageToken"] = token


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
        if not tree_id or bounds is None:
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

        rows.append(
            {
                "tree_id": tree_id,
                "city": city,
                "species": species,
                "tree_name": species,
                "plant_date": None,
                "diameter_at_breast_height": None,
                "latitude": latitude,
                "longitude": longitude,
            }
        )

    schema = pa.schema(
        [
            ("tree_id", pa.string()),
            ("city", pa.string()),
            ("species", pa.string()),
            ("tree_name", pa.string()),
            ("plant_date", pa.date32()),
            ("diameter_at_breast_height", pa.float64()),
            ("latitude", pa.float64()),
            ("longitude", pa.float64()),
        ]
    )
    table = pa.Table.from_pylist(rows, schema=schema)
    return enforce_tree_schema(table, city="Community")


if __name__ == "__main__":
    emit(records_to_table(load_published_records()))

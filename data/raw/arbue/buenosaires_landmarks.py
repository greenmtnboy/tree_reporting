#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy"]
# ///

import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit

CSV_PATH = Path(__file__).with_name("buenosaires_landmarks.csv")


def modified_at() -> datetime:
    if not CSV_PATH.exists():
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return datetime.fromtimestamp(CSV_PATH.stat().st_mtime, tz=timezone.utc)


def load_rows() -> list[dict[str, str]]:
    if not CSV_PATH.exists():
        return []
    with CSV_PATH.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [row for row in reader]


def build_table(rows: list[dict[str, str]]) -> pa.Table:
    updated_at = modified_at()
    landmark_ids: list[str] = []
    names: list[str] = []
    cities: list[str] = []
    geometries: list[str | None] = []
    updated_ats: list[datetime] = []

    for row in rows:
        landmark_id = (row.get("landmark_id") or "").strip()
        name = (row.get("name") or "").strip()
        city = (row.get("city") or "").strip()
        geometry_raw = (row.get("geometry_raw") or "").strip()

        if not landmark_id or not name or not city:
            continue

        landmark_ids.append(landmark_id)
        names.append(name)
        cities.append(city)
        geometries.append(geometry_raw or None)
        updated_ats.append(updated_at)

    return pa.table(
        {
            "landmark_id": pa.array(landmark_ids, type=pa.string()),
            "name": pa.array(names, type=pa.string()),
            "city": pa.array(cities, type=pa.string()),
            "geometry_raw": pa.array(geometries, type=pa.string()),
            "arbue_landmark_data_updated_through": pa.array(
                updated_ats, type=pa.timestamp("us", tz="UTC")
            ),
        }
    )


if __name__ == "__main__":
    emit(build_table(load_rows()))

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    emit,
    enforce_tree_schema,
    iter_offset_pages,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)

DATASET_URL = 'https://data.lacity.org/resource/vt5t-mscf.json'
PAGE_SIZE = 50000
PLACEHOLDER_NAMES = {'NULL', 'VACANT/INADEQUATE SPACING', 'STUMP'}


def parse_dbh(value: str | None) -> float | None:
    if not value or value == 'NULL':
        return None
    nums = [float(match) for match in re.findall(r'\d+(?:\.\d+)?', value)]
    if not nums:
        return None
    if len(nums) == 1:
        return nums[0]
    return sum(nums[:2]) / 2


def parse_plant_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def normalize_common_name(value: str | None) -> str | None:
    if not value or value.upper() in PLACEHOLDER_NAMES:
        return None
    return value.replace('/', ' / ').replace('  ', ' ').title().strip()


def normalize_botanical(value: str | None) -> str | None:
    if not value or value.upper() in PLACEHOLDER_NAMES:
        return None
    return normalize_species(value)


def iter_row_chunks():
    """One Socrata page at a time.

    A generator rather than a list: at 529,636 records this is the largest
    ingest in the repo after London, and holding them all as dicts is what
    made Amsterdam fail every cloud rebuild while passing locally.  See
    `_ingest_shared.stream_to_table`.
    """
    import requests

    session = requests.Session()

    def fetch_page(offset: int) -> list[dict]:
        response = session.get(
            DATASET_URL,
            params={
                '$select': 'trees_id,common,botanical,dbh,inv_date,x,y',
                '$limit': str(PAGE_SIZE),
                '$offset': str(offset),
            },
            timeout=120,
        )
        response.raise_for_status()
        return response.json()

    return iter_offset_pages(fetch_page, page_size=PAGE_SIZE)


def fetch_rows() -> list[dict]:
    """Every record at once, for callers that want it (analysis scripts)."""
    return [row for chunk in iter_row_chunks() for row in chunk]


def transform(rows: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    tree_name: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for row in rows:
        tree_id.append(f"lax-{row.get('trees_id')}" if row.get('trees_id') else None)
        botanical = normalize_botanical(row.get('botanical'))
        common = normalize_common_name(row.get('common'))
        species.append(botanical)
        tree_name.append(common or botanical)
        plant_date.append(parse_plant_date(row.get('inv_date')))
        try:
            lon = float(row.get('x')) if row.get('x') not in (None, '') else None
            lat = float(row.get('y')) if row.get('y') not in (None, '') else None
        except (TypeError, ValueError):
            lon, lat = None, None
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(parse_dbh(row.get('dbh')))

    n = len(rows)
    return pa.table({
        'tree_id': pa.array(tree_id, type=pa.string()),
        'city': pa.array(['USLAX'] * n, type=pa.string()),
        'species': pa.array(species, type=pa.string()),
        'tree_name': pa.array(tree_name, type=pa.string()),
        'plant_date': pa.array(plant_date, type=pa.date32()),
        'latitude': pa.array(latitude, type=pa.float64()),
        'longitude': pa.array(longitude, type=pa.float64()),
        'diameter_at_breast_height': pa.array(dbh, type=pa.float64()),
    })


if __name__ == '__main__':
    table = stream_to_table(
        iter_row_chunks(), transform, label='Los Angeles ingest'
    )
    before = table.num_rows
    table = table.filter(pc.is_valid(table['species']))
    dropped = before - table.num_rows
    if dropped:
        print(
            f"Los Angeles ingest: dropped {dropped} rows with null species",
            file=sys.stderr,
        )
    table = validate_coordinates(table, city='Los Angeles', city_code='USLAX')
    table = enforce_tree_schema(table, city='Los Angeles', data_source="LOSANGELES_OPENDATA")
    emit(table)

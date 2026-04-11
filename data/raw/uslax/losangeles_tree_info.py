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
from _ingest_shared import emit, normalize_species, validate_coordinates

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


def fetch_rows() -> list[dict]:
    import requests

    session = requests.Session()
    offset = 0
    rows: list[dict] = []
    while True:
        params = {
            '$select': 'trees_id,common,botanical,dbh,inv_date,x,y',
            '$limit': str(PAGE_SIZE),
            '$offset': str(offset),
        }
        response = session.get(DATASET_URL, params=params, timeout=120)
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


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
    table = transform(fetch_rows())
    before = table.num_rows
    table = table.filter(pc.is_valid(table['species']))
    dropped = before - table.num_rows
    if dropped:
        print(
            f"Los Angeles ingest: dropped {dropped} rows with null species",
            file=sys.stderr,
        )
    table = validate_coordinates(table, city='Los Angeles', city_code='USLAX')
    emit(table)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit, normalize_species, validate_coordinates

DATASET_URL = 'https://opendata.dc.gov/api/download/v1/items/f6c3c04113944f23a7993f2e603abaf2/geojson?layers=23'


def download_geojson() -> dict:
    import requests

    response = requests.get(DATASET_URL, timeout=300)
    response.raise_for_status()
    return response.json()


def parse_plant_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10].replace('/', '-'))
    except ValueError:
        return None


def normalize_common_name(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    return stripped.capitalize() if stripped.islower() else stripped


def transform(payload: dict) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    tree_name: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in payload['features']:
        props = feature.get('properties', {})
        if props.get('RETIREDDT') is not None:
            continue
        geom = feature.get('geometry') or {}
        coords = geom.get('coordinates') or [None, None]
        sci = normalize_species(props.get('SCI_NM'))
        common = normalize_common_name(props.get('CMMN_NM'))
        tree_id.append(f"was-{props.get('FACILITYID')}" if props.get('FACILITYID') else None)
        species.append(sci)
        tree_name.append(common or sci)
        plant_date.append(parse_plant_date(props.get('DATE_PLANT')))
        longitude.append(coords[0] if len(coords) > 0 else None)
        latitude.append(coords[1] if len(coords) > 1 else None)
        dbh.append(float(props['DBH']) if props.get('DBH') not in (None, '') else None)

    n = len(tree_id)
    return pa.table({
        'tree_id': pa.array(tree_id, type=pa.string()),
        'city': pa.array(['USWAS'] * n, type=pa.string()),
        'species': pa.array(species, type=pa.string()),
        'tree_name': pa.array(tree_name, type=pa.string()),
        'plant_date': pa.array(plant_date, type=pa.date32()),
        'latitude': pa.array(latitude, type=pa.float64()),
        'longitude': pa.array(longitude, type=pa.float64()),
        'diameter_at_breast_height': pa.array(dbh, type=pa.float64()),
    })


if __name__ == '__main__':
    table = transform(download_geojson())
    table = validate_coordinates(table, city='Washington DC', city_code='USWAS')
    emit(table)

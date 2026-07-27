#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit, enforce_tree_schema, normalize_species, validate_coordinates

DATASET_URL = 'https://data.tempe.gov/api/download/v1/items/542d8f16fff2466fb3115f209df03fd6/geojson?layers=0'


def download_geojson() -> dict:
    import requests

    response = requests.get(DATASET_URL, timeout=300)
    response.raise_for_status()
    return response.json()


def transform(payload: dict) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    tree_name: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in payload['features']:
        props = feature.get('properties', {})
        geom = feature.get('geometry') or {}
        coords = geom.get('coordinates') or [None, None]
        sci = normalize_species(props.get('Species_Name'))
        tree_id.append(f"tem-{props.get('Tree_ID')}" if props.get('Tree_ID') is not None else None)
        species.append(sci)
        tree_name.append(sci)
        longitude.append(coords[0] if len(coords) > 0 else None)
        latitude.append(coords[1] if len(coords) > 1 else None)
        dbh.append(float(props['DBH__in_']) if props.get('DBH__in_') is not None else None)

    n = len(tree_id)
    return pa.table({
        'tree_id': pa.array(tree_id, type=pa.string()),
        'city': pa.array(['USTEM'] * n, type=pa.string()),
        'species': pa.array(species, type=pa.string()),
        'tree_name': pa.array(tree_name, type=pa.string()),
        'plant_date': pa.array([None] * n, type=pa.date32()),
        'latitude': pa.array(latitude, type=pa.float64()),
        'longitude': pa.array(longitude, type=pa.float64()),
        'diameter_at_breast_height': pa.array(dbh, type=pa.float64()),
    })


if __name__ == '__main__':
    table = transform(download_geojson())
    table = validate_coordinates(table, city='Tempe', city_code='USTEM')
    table = enforce_tree_schema(table, city='Tempe')
    emit(table)

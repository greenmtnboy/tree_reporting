#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

import math
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit, enforce_tree_schema, normalize_species, validate_coordinates

DATASET_URL = 'https://data.tempe.gov/api/download/v1/items/542d8f16fff2466fb3115f209df03fd6/geojson?layers=0'

# Tempe serves this layer in Web Mercator metres, not the degrees GeoJSON
# normally implies — coordinates arrive as e.g. [-12460605.69, 3942222.75].
# Read as-is they are far outside the USTEM bounding box, so every row gets
# dropped and the city Parquet materialises empty.
WEB_MERCATOR = 'EPSG:3857'
EARTH_RADIUS_M = 6378137.0


def web_mercator_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """Convert EPSG:3857 metres to (longitude, latitude) degrees."""
    lon = math.degrees(x / EARTH_RADIUS_M)
    lat = math.degrees(2.0 * math.atan(math.exp(y / EARTH_RADIUS_M)) - math.pi / 2.0)
    return lon, lat


def download_geojson() -> dict:
    import requests

    response = requests.get(DATASET_URL, timeout=300)
    response.raise_for_status()
    return response.json()


def transform(payload: dict) -> pa.Table:
    # Fail loudly if the portal ever changes projection: silently trusting the
    # numbers is what made this ship an empty city.
    crs_name = ((payload.get('crs') or {}).get('properties') or {}).get('name')
    if crs_name != WEB_MERCATOR:
        raise ValueError(
            f'Tempe ingest: expected {WEB_MERCATOR} coordinates, got {crs_name!r}'
        )

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
        if len(coords) > 1 and coords[0] is not None and coords[1] is not None:
            lon, lat = web_mercator_to_wgs84(coords[0], coords[1])
        else:
            lon, lat = None, None
        longitude.append(lon)
        latitude.append(lat)
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
    table = enforce_tree_schema(table, city='Tempe', data_source="TEMPE_OPENDATA")
    emit(table)

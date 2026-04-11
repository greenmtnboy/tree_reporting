#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

import io
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pv

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import cm_to_inches, emit, normalize_species, validate_coordinates

DATASET_URL = 'https://cdn.buenosaires.gob.ar/datosabiertos/datasets/atencion-ciudadana/arbolado-publico-lineal/arbolado-publico-lineal-2017-2018.csv'


def download_csv() -> io.BytesIO:
    import requests

    response = requests.get(DATASET_URL, stream=True, timeout=300)
    response.raise_for_status()
    buf = io.BytesIO()
    for chunk in response.iter_content(chunk_size=1024 * 1024):
        if chunk:
            buf.write(chunk)
    buf.seek(0)
    return buf


def load_table(csv_bytes: io.BytesIO) -> pa.Table:
    return pv.read_csv(
        csv_bytes,
        convert_options=pv.ConvertOptions(
            strings_can_be_null=True,
            column_types={
                'long': pa.string(),
                'lat': pa.string(),
                'nro_registro': pa.string(),
                'nombre_cientifico': pa.string(),
                'diametro_altura_pecho': pa.string(),
            },
        ),
    )


def transform(table: pa.Table) -> pa.Table:
    n = table.num_rows
    ids = table['nro_registro'].to_pylist()
    species_raw = table['nombre_cientifico'].to_pylist()
    diam_raw = table['diametro_altura_pecho'].to_pylist()
    lat_raw = table['lat'].to_pylist()
    lon_raw = table['long'].to_pylist()

    return pa.table({
        'tree_id': pa.array([f'bue-{value}' if value else None for value in ids], type=pa.string()),
        'city': pa.array(['ARBUE'] * n, type=pa.string()),
        'species': pa.array([normalize_species(value) for value in species_raw], type=pa.string()),
        'tree_name': pa.array([normalize_species(value) for value in species_raw], type=pa.string()),
        'plant_date': pa.array([None] * n, type=pa.date32()),
        'latitude': pa.array([float(value) if value not in (None, '') else None for value in lat_raw], type=pa.float64()),
        'longitude': pa.array([float(value) if value not in (None, '') else None for value in lon_raw], type=pa.float64()),
        'diameter_at_breast_height': pa.array([cm_to_inches(value) for value in diam_raw], type=pa.float64()),
    })


if __name__ == '__main__':
    table = transform(load_table(download_csv()))
    table = validate_coordinates(table, city='Buenos Aires', city_code='ARBUE')
    emit(table)

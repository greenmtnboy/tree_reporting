#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

"""
Fetch Paris tree data from opendata.paris.fr and emit Arrow IPC to stdout.

Source: https://opendata.paris.fr/explore/dataset/les-arbres/information/
API:    https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/les-arbres/exports/parquet

Field mapping:
  idbase            -> tree_id  (prefixed "par-" for global uniqueness)
  genre + espece    -> species  (scientific name only: "Genre espece")
  circonferenceencm -> diameter_at_breast_height (circumference cm → diameter inches)
  geo_point_2d      -> latitude, longitude
"""

import io
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    emit,
    normalize_species,
    validate_coordinates,
    download_parquet as _download_parquet,
    parse_wkb_point,
    circumference_cm_to_dbh_inches,
)

# OpenDataSoft v2 parquet export — full dataset, no pagination needed
DATASET_URL = (
    "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/"
    "les-arbres/exports/parquet"
    "?select=idbase%2Cgenre%2Cespece%2Clibellefrancais"
    "%2Ccirconferenceencm%2Cgeo_point_2d&lang=en&timezone=UTC"
)


def download_parquet() -> io.BytesIO:
    return _download_parquet(DATASET_URL, timeout=180)


def transform(table: pa.Table) -> pa.Table:
    names = table.schema.names

    # --- tree_id: prefix idbase with "par-" ---
    idbase_col = next((c for c in names if c.lower() == "idbase"), None)
    ids = table[idbase_col].to_pylist() if idbase_col else [None] * table.num_rows
    tree_id = pa.array([f"par-{v}" if v is not None else None for v in ids], type=pa.string())

    # --- species: "Genre espece" (scientific name only) ---
    genre_col = next((c for c in names if c.lower() == "genre"), None)
    espece_col = next((c for c in names if c.lower() == "espece"), None)
    libelle_col = next((c for c in names if c.lower() == "libellefrancais"), None)

    genre_list = table[genre_col].to_pylist() if genre_col else [None] * table.num_rows
    espece_list = table[espece_col].to_pylist() if espece_col else [None] * table.num_rows
    libelle_list = table[libelle_col].to_pylist() if libelle_col else [None] * table.num_rows

    species = pa.array(
        [
            normalize_species(f"{(g or '').strip()} {(e or '').strip()}")
            for g, e in zip(genre_list, espece_list)
        ],
        type=pa.string(),
    )

    species_list = species.to_pylist()
    tree_name = pa.array(
        [(v.strip() if v and v.strip() else None) or s for v, s in zip(libelle_list, species_list)],
        type=pa.string(),
    )

    # --- lat/lon from geo_point_2d (WKB binary: byte_order + uint32 type + double x + double y) ---
    geo_col = next((c for c in names if c.lower() == "geo_point_2d"), None)
    if geo_col is not None:
        lons, lats = [], []
        for wkb in table[geo_col].to_pylist():
            x, y = parse_wkb_point(wkb)
            lons.append(x)
            lats.append(y)
        lat = pa.array(lats, type=pa.float64())
        lon = pa.array(lons, type=pa.float64())
    else:
        lat = pa.array([None] * table.num_rows, type=pa.float64())
        lon = pa.array([None] * table.num_rows, type=pa.float64())

    # --- diameter: circumference (cm) → diameter (inches) ---
    circ_col = next((c for c in names if c.lower() == "circonferenceencm"), None)
    circ_list = table[circ_col].to_pylist() if circ_col else [None] * table.num_rows
    dbh = pa.array(
        [circumference_cm_to_dbh_inches(v) for v in circ_list],
        type=pa.float64(),
    )

    n = table.num_rows
    return pa.table(
        {
            "tree_id": tree_id,
            "city": pa.array(["FRPAR"] * n, type=pa.string()),
            "species": species,
            "tree_name": tree_name,
            "plant_date": pa.array([None] * n, type=pa.null()),
            "latitude": lat,
            "longitude": lon,
            "diameter_at_breast_height": dbh,
        }
    )


def validate(table: pa.Table) -> None:
    validate_coordinates(table, city="Paris")


if __name__ == "__main__":
    buf = download_parquet()
    raw = pq.read_table(buf)
    table = transform(raw)
    validate(table)
    emit(table)

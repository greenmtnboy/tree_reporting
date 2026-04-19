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
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
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


def _row_score(row: dict[str, Any]) -> tuple[int, float]:
    populated = sum(
        1
        for key in (
            "genre",
            "espece",
            "libellefrancais",
            "circonferenceencm",
            "geo_point_2d",
        )
        if row.get(key) not in (None, "")
    )
    circumference = row.get("circonferenceencm")
    if isinstance(circumference, (int, float)):
        size = float(circumference)
    else:
        size = -1.0
    return (populated, size)


def _dedupe_rows(table: pa.Table) -> list[dict[str, Any]]:
    rows = table.to_pylist()
    deduped: dict[Any, dict[str, Any]] = {}
    for row in rows:
        key = row.get("idbase")
        if key is None:
            continue
        existing = deduped.get(key)
        if existing is None or _row_score(row) > _row_score(existing):
            deduped[key] = row
    return list(deduped.values())


def transform(table: pa.Table) -> pa.Table:
    rows = _dedupe_rows(table)
    if not rows:
        return pa.table(
            {
                "tree_id": pa.array([], type=pa.string()),
                "city": pa.array([], type=pa.string()),
                "species": pa.array([], type=pa.string()),
                "tree_name": pa.array([], type=pa.string()),
                "plant_date": pa.array([], type=pa.null()),
                "latitude": pa.array([], type=pa.float64()),
                "longitude": pa.array([], type=pa.float64()),
                "diameter_at_breast_height": pa.array([], type=pa.float64()),
            }
        )

    # --- tree_id: prefix idbase with "par-" ---
    ids = [row.get("idbase") for row in rows]
    tree_id = pa.array(
        [f"par-{value}" if value is not None else None for value in ids],
        type=pa.string(),
    )

    # --- species: "Genre espece" (scientific name only) ---
    genre_list = [row.get("genre") for row in rows]
    espece_list = [row.get("espece") for row in rows]
    libelle_list = [row.get("libellefrancais") for row in rows]

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
    lons, lats = [], []
    for wkb in [row.get("geo_point_2d") for row in rows]:
        x, y = parse_wkb_point(wkb)
        lons.append(x)
        lats.append(y)
    lat = pa.array(lats, type=pa.float64())
    lon = pa.array(lons, type=pa.float64())

    # --- diameter: circumference (cm) → diameter (inches) ---
    circ_list = [row.get("circonferenceencm") for row in rows]
    dbh = pa.array(
        [circumference_cm_to_dbh_inches(v) for v in circ_list],
        type=pa.float64(),
    )

    n = len(rows)
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


if __name__ == "__main__":
    buf = download_parquet()
    raw = pq.read_table(buf)
    table = transform(raw)
    table = validate_coordinates(table, city="Paris", city_code="FRPAR")
    emit(table)

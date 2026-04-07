#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

"""
Fetch Vancouver public tree data from opendata.vancouver.ca and emit Arrow IPC to stdout.

Source: https://opendata.vancouver.ca/explore/dataset/public-trees/information/
API:    https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets/public-trees/exports/parquet

Field mapping:
  asset_id     -> tree_id  (prefixed "van-" for global uniqueness)
  genus_name   -> combined with species_name for scientific name
  species_name -> species epithet (combined with genus_name)
  common_name  -> tree_name
  diameter_cm  -> diameter_at_breast_height (cm → inches: divide by 2.54)
  date_planted -> plant_date
  geo_point_2d -> latitude, longitude  (WKB binary)
"""

import io
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import requests
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    emit,
    normalize_species_parts,
    validate_coordinates,
    download_parquet as _download_parquet,
    parse_wkb_point,
)

# OpenDataSoft v2 parquet export — full dataset, no pagination needed
DATASET_URL = (
    "https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets/"
    "public-trees/exports/parquet"
    "?select=asset_id%2Cgenus_name%2Cspecies_name%2Ccommon_name"
    "%2Cdiameter_cm%2Cdate_planted%2Cgeo_point_2d&lang=en&timezone=UTC"
)


def download_parquet() -> io.BytesIO:
    return _download_parquet(DATASET_URL, timeout=180)


def transform(table: pa.Table) -> pa.Table:
    names = table.schema.names
    n = table.num_rows

    # --- tree_id: prefix asset_id with "van-" ---
    id_col = next((c for c in names if c.lower() == "asset_id"), None)
    ids = table[id_col].to_pylist() if id_col else [None] * n
    tree_id = pa.array([f"van-{v}" if v is not None else None for v in ids], type=pa.string())

    # --- species: "Genus epithet" (scientific name only) ---
    genus_col = next((c for c in names if c.lower() == "genus_name"), None)
    epithet_col = next((c for c in names if c.lower() == "species_name"), None)
    genus_list = table[genus_col].to_pylist() if genus_col else [None] * n
    epithet_list = table[epithet_col].to_pylist() if epithet_col else [None] * n

    species = pa.array(
        [normalize_species_parts(g, e) for g, e in zip(genus_list, epithet_list)],
        type=pa.string(),
    )

    # --- tree_name: common_name ---
    cn_col = next((c for c in names if c.lower() == "common_name"), None)
    cn_list = table[cn_col].to_pylist() if cn_col else [None] * n
    tree_name = pa.array(
        [v.strip() if v and v.strip() else None for v in cn_list],
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
        lat = pa.array([None] * n, type=pa.float64())
        lon = pa.array([None] * n, type=pa.float64())

    # --- diameter: cm → inches (divide by 2.54) ---
    diam_col = next((c for c in names if c.lower() == "diameter_cm"), None)
    diam_list = table[diam_col].to_pylist() if diam_col else [None] * n
    dbh = pa.array(
        [float(v) / 2.54 if v is not None else None for v in diam_list],
        type=pa.float64(),
    )

    # --- plant_date: date_planted (already a date field from OpenDataSoft) ---
    date_col = next((c for c in names if c.lower() == "date_planted"), None)
    if date_col is not None:
        plant_date = table[date_col].cast(pa.date32())
    else:
        plant_date = pa.array([None] * n, type=pa.date32())

    return pa.table(
        {
            "tree_id": tree_id,
            "city": pa.array(["CAVAN"] * n, type=pa.string()),
            "species": species,
            "tree_name": tree_name,
            "plant_date": plant_date,
            "latitude": lat,
            "longitude": lon,
            "diameter_at_breast_height": dbh,
        }
    )


if __name__ == "__main__":
    buf = download_parquet()
    raw = pq.read_table(buf)
    table = transform(raw)
    table = validate_coordinates(table, city="Vancouver", city_code="CAVAN")
    emit(table)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

"""
Fetch Vancouver heritage sites from opendata.vancouver.ca and emit Arrow IPC to stdout.

Source: https://opendata.vancouver.ca/explore/dataset/heritage-sites/information/
Dataset: Heritage Sites — buildings, streetscapes, and landscape resources on the
         Vancouver Heritage Register (~2,495 records).

Field mapping:
  id                   -> landmark_id  (prefixed "cavan-")
  buildingnamespecifics -> name
  geo_point_2d         -> geometry_raw (WKT POINT), latitude, longitude
  category             -> category  (Vancouver-specific: building type/class)
  evaluationgroup      -> evaluation_group  (A, B, or C)
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
    validate_coordinates,
    download_parquet as _download_parquet,
    parse_wkb_point,
    make_point_wkt,
)

DATASET_URL = (
    "https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets/"
    "heritage-sites/exports/parquet"
    "?select=id%2Cbuildingnamespecifics%2Ccategory%2Cevaluationgroup%2Cgeo_point_2d"
    "&lang=en&timezone=UTC"
)


def download_parquet() -> io.BytesIO:
    return _download_parquet(DATASET_URL, timeout=180)


def transform(table: pa.Table) -> pa.Table:
    names = table.schema.names
    n = table.num_rows

    # --- landmark_id: prefix id with "cavan-" ---
    id_col = next((c for c in names if c.lower() == "id"), None)
    ids = table[id_col].to_pylist() if id_col else [None] * n
    landmark_id = pa.array(
        [f"cavan-{v}" if v is not None else None for v in ids],
        type=pa.string(),
    )

    # --- name: buildingnamespecifics ---
    name_col = next((c for c in names if c.lower() == "buildingnamespecifics"), None)
    raw_names = table[name_col].to_pylist() if name_col else [None] * n
    name = pa.array(
        [v.strip() if v and v.strip() else None for v in raw_names],
        type=pa.string(),
    )

    # --- lat/lon + geometry_raw from geo_point_2d (WKB binary) ---
    geo_col = next((c for c in names if c.lower() == "geo_point_2d"), None)
    if geo_col is not None:
        lon_list, lat_list = [], []
        for wkb in table[geo_col].to_pylist():
            x, y = parse_wkb_point(wkb)
            lon_list.append(x)
            lat_list.append(y)
    else:
        lat_list = [None] * n
        lon_list = [None] * n

    lat = pa.array(lat_list, type=pa.float64())
    lon = pa.array(lon_list, type=pa.float64())
    geometry_raw = pa.array(
        [make_point_wkt(lo, la) for lo, la in zip(lon_list, lat_list)],
        type=pa.string(),
    )

    # --- Vancouver-specific fields ---
    cat_col = next((c for c in names if c.lower() == "category"), None)
    category = (
        table[cat_col] if cat_col
        else pa.array([None] * n, type=pa.string())
    )

    eval_col = next((c for c in names if c.lower() == "evaluationgroup"), None)
    evaluation_group = (
        table[eval_col] if eval_col
        else pa.array([None] * n, type=pa.string())
    )

    return pa.table(
        {
            "landmark_id": landmark_id,
            "city": pa.array(["CAVAN"] * n, type=pa.string()),
            "name": name,
            "geometry_raw": geometry_raw,
            "latitude": lat,
            "longitude": lon,
            "category": category,
            "evaluation_group": evaluation_group,
        }
    )


def validate(table: pa.Table) -> None:
    validate_coordinates(table, city="Vancouver landmarks")


if __name__ == "__main__":
    buf = download_parquet()
    raw = pq.read_table(buf)
    table = transform(raw)
    validate(table)
    emit(table)

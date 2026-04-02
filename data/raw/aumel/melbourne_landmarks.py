#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

"""
Fetch Melbourne landmarks and places of interest from the City of Melbourne
Open Data portal (OpenDataSoft v2) and emit Arrow IPC to stdout.

Source: https://data.melbourne.vic.gov.au/explore/dataset/landmarks-and-places-of-interest-including-schools-theatres-health-services-spor/
~242 records covering theatres, galleries, museums, railway stations, parks, and
other significant public places within the City of Melbourne.

Field mapping:
  row index (zero-padded)         -> landmark_id (prefixed "aumel-")
  feature_name                    -> name
  co_ordinates.lon/lat            -> longitude, latitude, geometry_raw (POINT WKT)
"""

import sys
import requests
import pyarrow as pa
import pyarrow.parquet as pq
import io
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    emit,
    validate_coordinates,
    download_parquet as _download_parquet,
    parse_wkb_point,
    make_point_wkt,
)

DATASET_ID = (
    "landmarks-and-places-of-interest-including-schools-theatres-health-services-spor"
)
PARQUET_URL = (
    f"https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/"
    f"{DATASET_ID}/exports/parquet"
    "?lang=en&timezone=UTC"
)


def download_parquet() -> io.BytesIO:
    return _download_parquet(PARQUET_URL, timeout=120)


def transform(table: pa.Table) -> pa.Table:
    names_col = table.schema.names
    feature_col = next((c for c in names_col if c.lower() == "feature_name"), None)
    coord_col = next((c for c in names_col if c.lower() == "co_ordinates"), None)

    feature_names = table[feature_col].to_pylist() if feature_col else [None] * table.num_rows
    n = table.num_rows

    # co_ordinates is a WKB geo_point_2d field on OpenDataSoft portals
    lons, lats = [], []
    if coord_col is not None:
        for wkb in table[coord_col].to_pylist():
            x, y = parse_wkb_point(wkb)
            lons.append(x)
            lats.append(y)
    else:
        lons = [None] * n
        lats = [None] * n

    landmark_ids = [f"aumel-{i:04d}" for i in range(n)]
    cities = ["AUMEL"] * n
    geom_raws = [make_point_wkt(lo, la) for lo, la in zip(lons, lats)]

    return pa.table(
        {
            "landmark_id": pa.array(landmark_ids, type=pa.string()),
            "city": pa.array(cities, type=pa.string()),
            "name": pa.array(feature_names, type=pa.string()),
            "geometry_raw": pa.array(geom_raws, type=pa.string()),
            "latitude": pa.array(lats, type=pa.float64()),
            "longitude": pa.array(lons, type=pa.float64()),
        }
    )


def validate(table: pa.Table) -> None:
    validate_coordinates(table, city="Melbourne landmarks")


if __name__ == "__main__":
    buf = download_parquet()
    raw = pq.read_table(buf)
    table = transform(raw)
    validate(table)
    emit(table)

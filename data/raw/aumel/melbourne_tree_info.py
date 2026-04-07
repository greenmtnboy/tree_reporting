#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

"""
Fetch Melbourne Urban Forest tree data from data.melbourne.vic.gov.au and emit
Arrow IPC to stdout.

Source: https://data.melbourne.vic.gov.au/explore/dataset/trees-with-species-and-dimensions-urban-forest/
API:    https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/trees-with-species-and-dimensions-urban-forest/exports/parquet

Field mapping:
  com_id           -> tree_id  (prefixed "mel-" for global uniqueness)
  genus + species  -> scientific name (combined as "Genus epithet")
  common_name      -> tree_name
  dbh              -> diameter_at_breast_height (cm → inches: divide by 2.54)
  date_planted     -> plant_date
  geo_point_2d     -> latitude, longitude  (WKB binary)
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

# OpenDataSoft v2 parquet export — select only needed fields to reduce download size
DATASET_URL = (
    "https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/"
    "trees-with-species-and-dimensions-urban-forest/exports/parquet"
    "?lang=en&timezone=UTC"
)


def download_parquet() -> io.BytesIO:
    return _download_parquet(DATASET_URL, timeout=300)


def transform(table: pa.Table) -> pa.Table:
    names = table.schema.names
    n = table.num_rows

    # --- tree_id: prefix com_id with "mel-" ---
    id_col = next(
        (c for c in names if c.lower() in ("com_id", "id", "tree_id")), None
    )
    ids = table[id_col].to_pylist() if id_col else [None] * n
    tree_id = pa.array(
        [f"mel-{v}" if v is not None else None for v in ids],
        type=pa.string(),
    )

    # --- species: "Genus epithet" (scientific name only) ---
    genus_col = next((c for c in names if c.lower() == "genus"), None)
    epithet_col = next(
        (c for c in names if c.lower() in ("species", "species_name", "epithet")),
        None,
    )
    # Some datasets provide a pre-combined scientific_name field
    sci_col = next(
        (c for c in names if c.lower() in ("scientific_name", "species_scientific")),
        None,
    )

    if sci_col is not None and genus_col is None:
        # Use pre-combined scientific name directly
        sci_list = table[sci_col].to_pylist()
        species = pa.array(
            [v.strip() if v and v.strip() else None for v in sci_list],
            type=pa.string(),
        )
    else:
        genus_list = table[genus_col].to_pylist() if genus_col else [None] * n
        epithet_list = table[epithet_col].to_pylist() if epithet_col else [None] * n
        species = pa.array(
            [normalize_species_parts(g, e) for g, e in zip(genus_list, epithet_list)],
            type=pa.string(),
        )

    # --- tree_name: common_name ---
    cn_col = next(
        (c for c in names if c.lower() in ("common_name", "commonname", "tree_name")),
        None,
    )
    cn_list = table[cn_col].to_pylist() if cn_col else [None] * n
    tree_name = pa.array(
        [v.strip() if v and v.strip() else None for v in cn_list],
        type=pa.string(),
    )

    # --- lat/lon from geo_point_2d (WKB binary: byte_order + uint32 type + double x + double y) ---
    # OpenDataSoft exports geo_point_2d as WKB binary; x=longitude, y=latitude
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
        # Fallback: look for separate lat/lon columns
        lat_col = next((c for c in names if c.lower() in ("latitude", "lat")), None)
        lon_col = next((c for c in names if c.lower() in ("longitude", "lon", "lng")), None)
        lat = (
            table[lat_col].cast(pa.float64()) if lat_col
            else pa.array([None] * n, type=pa.float64())
        )
        lon = (
            table[lon_col].cast(pa.float64()) if lon_col
            else pa.array([None] * n, type=pa.float64())
        )

    # --- diameter: dbh (Melbourne reports in cm → convert to inches) ---
    diam_col = next(
        (c for c in names if c.lower() in ("dbh", "diameter_breast_height", "diameter_cm")),
        None,
    )
    diam_list = table[diam_col].to_pylist() if diam_col else [None] * n
    dbh = pa.array(
        [float(v) / 2.54 if v is not None else None for v in diam_list],
        type=pa.float64(),
    )

    # --- plant_date: date_planted ---
    date_col = next(
        (c for c in names if c.lower() in ("date_planted", "year_planted", "plant_date")),
        None,
    )
    if date_col is not None:
        raw_dates = table[date_col]
        try:
            plant_date = raw_dates.cast(pa.date32())
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
            # year_planted may be an integer year — convert to Jan 1 of that year
            year_list = raw_dates.to_pylist()
            from datetime import date
            plant_date = pa.array(
                [
                    date(int(y), 1, 1) if y is not None else None
                    for y in year_list
                ],
                type=pa.date32(),
            )
    else:
        plant_date = pa.array([None] * n, type=pa.date32())

    return pa.table(
        {
            "tree_id": tree_id,
            "city": pa.array(["AUMEL"] * n, type=pa.string()),
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
    table = validate_coordinates(table, city="Melbourne", city_code="AUMEL")
    emit(table)

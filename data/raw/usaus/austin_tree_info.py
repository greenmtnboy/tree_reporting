#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Austin's municipal tree inventory.  STUB -- fill in the field mapping.

Emit one Arrow IPC stream on stdout with the canonical tree schema.  The parts
that are not optional:

  * `species` is the Latin binomial ONLY -- strip any "::" common-name suffix,
    or combine separate genus/epithet fields with `normalize_species_parts`.
  * `plant_date` is a real `date32` even when every value is null; an untyped
    `pa.null()` column lands in the parquet as INT32 and breaks `year()`.
  * `enforce_tree_schema` immediately before `emit`, always.  It is the single
    chokepoint for column types and species hygiene.
  * `validate_coordinates` before it, so a wrong-hemisphere geocode is caught
    here rather than as a dot in the ocean.

For an ArcGIS FeatureServer source, `shared.platforms.arcgis` already has the paging,
the watermark and the Esri epoch conversion -- see usden/denver_tree_info.py.
For a large source, stream it: `stream_to_table` over a chunk generator keeps
peak memory at one chunk (a bulk `response.json()` OOM-killed a 2 GiB
container at 216k features).
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    emit,
    enforce_tree_schema,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)

SOURCE_URL = "TODO: the portal endpoint this city publishes"


def iter_row_chunks():
    """Yield lists of source records, one page at a time."""
    raise NotImplementedError("TODO: page Austin's portal")


def transform(rows: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    tree_name: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for rec in rows:
        raise NotImplementedError("TODO: map Austin's fields")

    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["USAUS"] * len(tree_id), type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "tree_name": pa.array(tree_name, type=pa.string()),
        "plant_date": pa.array(plant_date, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Austin OpenData")
    table = validate_coordinates(table, city="Austin", city_code="USAUS")
    table = enforce_tree_schema(
        table, city="Austin", data_source="AUSTIN_OPENDATA"
    )
    emit(table)

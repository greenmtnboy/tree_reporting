#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

import io
import re
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pv
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    stream_table_batches,
    emit,
    enforce_tree_schema,
    normalize_species,
    validate_coordinates,
)

DATASET_ID = "hn5i-inap"
DATASET_URL = f"https://data.cityofnewyork.us/resource/{DATASET_ID}.csv"
PAGE_SIZE = 500000
DATASET_PARAMS = {
    "$select": "objectid,genusspecies,dbh,planteddate,location",
    "$limit": str(PAGE_SIZE),
}

# Socrata exports point columns as WKT: "POINT (lon lat)"
_POINT_RE = re.compile(r"POINT\s*\(\s*([+-]?\d+\.?\d*)\s+([+-]?\d+\.?\d*)\s*\)")


def download_csv_page(offset: int = 0) -> io.BytesIO:
    params = DATASET_PARAMS | {"$offset": str(offset)}
    r = requests.get(DATASET_URL, params=params, stream=True)
    r.raise_for_status()

    buf = io.BytesIO()
    for chunk in r.iter_content(chunk_size=1024 * 1024):
        if chunk:
            buf.write(chunk)
    buf.seek(0)
    return buf


def parse_point_column(table: pa.Table, col: str) -> tuple[pa.Array, pa.Array]:
    """Parse a WKT POINT column into separate longitude and latitude arrays."""
    lons = []
    lats = []
    for val in table[col].to_pylist():
        if val is None:
            lons.append(None)
            lats.append(None)
        else:
            m = _POINT_RE.match(val.strip())
            if m:
                lons.append(float(m.group(1)))
                lats.append(float(m.group(2)))
            else:
                lons.append(None)
                lats.append(None)
    return pa.array(lons, type=pa.float64()), pa.array(lats, type=pa.float64())


def cast_columns(table: pa.Table) -> pa.Table:
    # Parse WKT location point into latitude/longitude.
    loc_col = next((c for c in table.schema.names if c.lower() == "location"), None)
    if loc_col is not None:
        lons, lats = parse_point_column(table, loc_col)
        table = table.append_column("longitude", lons)
        table = table.append_column("latitude", lats)
        table = table.remove_column(table.schema.get_field_index(loc_col))

    # Parse planteddate from ISO-8601 text.
    planted_col = next((c for c in table.schema.names if c.lower() == "planteddate"), None)
    if planted_col is not None:
        idx = table.schema.get_field_index(planted_col)
        date_str = pc.utf8_slice_codeunits(table[planted_col], 0, 10)
        ts = pc.strptime(date_str, format="%Y-%m-%d", unit="s")
        table = table.set_column(idx, planted_col, pc.cast(ts, pa.date32()))

    # objectid: prefix with "nyc-" for global uniqueness across cities.
    id_col = next((c for c in table.schema.names if c.lower() == "objectid"), None)
    if id_col is not None:
        ids = table[id_col].to_pylist()
        prefixed = pa.array(
            [f"nyc-{v}" if v is not None else None for v in ids],
            type=pa.string(),
        )
        table = table.set_column(table.schema.get_field_index(id_col), id_col, prefixed)

    # dbh arrives as text; parse to the canonical float64 (see enforce_tree_schema).
    dbh_col = next((c for c in table.schema.names if c.lower() == "dbh"), None)
    if dbh_col is not None:
        idx = table.schema.get_field_index(dbh_col)
        table = table.set_column(idx, dbh_col, pc.cast(table[dbh_col], pa.float64()))

    # genusspecies: split "scientific - common name" and store each part separately.
    genus_col = next((c for c in table.schema.names if c.lower() == "genusspecies"), None)
    if genus_col is not None:
        species_list = table[genus_col].to_pylist()
        scientific = pa.array(
            [
                normalize_species(v.split(" - ")[0]) if v is not None else None
                for v in species_list
            ],
            type=pa.string(),
        )
        sci_list = scientific.to_pylist()
        tree_name = pa.array(
            [
                (
                    v.split(" - ", 1)[1].strip().title()
                    if v is not None and " - " in v
                    else None
                )
                or s
                for v, s in zip(species_list, sci_list)
            ],
            type=pa.string(),
        )
        table = table.set_column(
            table.schema.get_field_index(genus_col), genus_col, scientific
        )
        table = table.append_column("tree_name", tree_name)

    # Normalize all column names to lowercase to match preql mappings.
    table = table.rename_columns([c.lower() for c in table.schema.names])

    return table


def load_arrow_table(csv_bytes: io.BytesIO) -> tuple[pa.Table, int]:
    table = pv.read_csv(
        csv_bytes,
        convert_options=pv.ConvertOptions(
            strings_can_be_null=True,
            # Keep date/numeric columns as strings for manual casting.
            column_types={
                "planteddate": pa.string(),
                "objectid": pa.string(),
                "dbh": pa.string(),
            },
        ),
    )
    raw_count = table.num_rows
    # Batched: PAGE_SIZE is 500,000 and `cast_columns` calls `.to_pylist()`
    # on each column, so a single page materialised more Python objects
    # than Amsterdam's entire dataset did before it started failing every
    # cloud rebuild. See _ingest_shared.stream_table_batches.
    table = stream_table_batches(table, cast_columns, label='New York City page')
    # No null-species filter. A null species means "we do not know what this
    # tree is", not "this is not a tree", and `enforce_tree_schema` gives those
    # rows the UNKNOWN_SPECIES sentinel like every other city's. The same
    # filter cost Amsterdam 17,433 real trees and LA 5,479; NYC only 36, but
    # the rule should not have an exception left in it.
    return table, raw_count


def load_all_arrow_tables() -> pa.Table:
    tables: list[pa.Table] = []
    offset = 0

    while True:
        csv_bytes = download_csv_page(offset)
        table, raw_count = load_arrow_table(csv_bytes)
        tables.append(table)
        if raw_count < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return pa.concat_tables(tables)


def add_city_column(table: pa.Table) -> pa.Table:
    return table.append_column(
        "city", pa.array(["USNYC"] * table.num_rows, type=pa.string())
    )


if __name__ == "__main__":
    table = load_all_arrow_tables()
    table = add_city_column(table)
    table = validate_coordinates(table, city="New York City", city_code="USNYC")
    table = enforce_tree_schema(
        table,
        city="New York City",
        data_source="NYC_OPENDATA",
        columns={
            "tree_id": "objectid",
            "species": "genusspecies",
            "plant_date": "planteddate",
            "diameter_at_breast_height": "dbh",
        },
    )
    emit(table)

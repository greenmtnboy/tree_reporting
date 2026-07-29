#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

import io
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pv
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    emit,
    enforce_tree_schema,
    normalize_species,
    validate_coordinates,
)

DATASET_ID = "tkzw-k3nq"
DATASET_URL = f"https://data.sfgov.org/resource/{DATASET_ID}.csv"
DATASET_PARAMS = {
    "$select": "treeid,qspecies,plantdate,dbh,latitude,longitude,planttype",
    "$limit": "500000",
}


def download_csv() -> io.BytesIO:
    r = requests.get(DATASET_URL, params=DATASET_PARAMS, stream=True)
    r.raise_for_status()

    buf = io.BytesIO()
    for chunk in r.iter_content(chunk_size=1024 * 1024):
        if chunk:
            buf.write(chunk)
    buf.seek(0)
    return buf


def cast_columns(table: pa.Table) -> pa.Table:
    # treeid: prefix with "sf-" for global uniqueness across cities
    if "treeid" in table.schema.names:
        ids = table["treeid"].to_pylist()
        prefixed = pa.array(
            [f"sf-{v}" if v is not None else None for v in ids],
            type=pa.string(),
        )
        table = table.set_column(
            table.schema.get_field_index("treeid"), "treeid", prefixed
        )

    # plantdate comes from the Socrata resource endpoint as ISO-8601 text.
    if "plantdate" in table.schema.names:
        date_str = pc.utf8_slice_codeunits(table["plantdate"], 0, 10)
        ts = pc.strptime(date_str, format="%Y-%m-%d", unit="s")
        table = table.set_column(
            table.schema.get_field_index("plantdate"),
            "plantdate",
            pc.cast(ts, pa.date32()),
        )

    # qspecies: split "scientific :: common name" and store each part separately.
    if "qspecies" in table.schema.names:
        species_list = table["qspecies"].to_pylist()
        scientific = pa.array(
            [
                normalize_species(v.split("::")[0]) if v is not None else None
                for v in species_list
            ],
            type=pa.string(),
        )
        sci_list = scientific.to_pylist()
        tree_name = pa.array(
            [
                (
                    v.split("::", 1)[1].strip()
                    if v is not None and "::" in v
                    else None
                )
                or s
                for v, s in zip(species_list, sci_list)
            ],
            type=pa.string(),
        )
        table = table.set_column(
            table.schema.get_field_index("qspecies"), "qspecies", scientific
        )
        table = table.append_column("tree_name", tree_name)

    return table


def load_arrow_table(csv_bytes: io.BytesIO) -> pa.Table:
    table = pv.read_csv(
        csv_bytes,
        convert_options=pv.ConvertOptions(
            strings_can_be_null=True,
            # Keep plantdate as a string so we can cast it ourselves.
            column_types={"plantdate": pa.string()},
        ),
    )
    table = table.rename_columns([c.lower() for c in table.schema.names])

    # Filter to trees only before any further processing.
    if "planttype" in table.schema.names:
        table = table.filter(pc.equal(table["planttype"], "Tree"))

    table = cast_columns(table)

    if "qspecies" in table.schema.names:
        before = table.num_rows
        table = table.filter(pc.is_valid(table["qspecies"]))
        dropped = before - table.num_rows
        if dropped:
            print(
                f"San Francisco ingest: dropped {dropped} rows with null species",
                file=sys.stderr,
            )

    return table


def add_city_column(table: pa.Table) -> pa.Table:
    return table.append_column(
        "city", pa.array(["USSFO"] * table.num_rows, type=pa.string())
    )


if __name__ == "__main__":
    csv_bytes = download_csv()
    table = load_arrow_table(csv_bytes)
    table = add_city_column(table)
    table = validate_coordinates(table, city="San Francisco", city_code="USSFO")
    table = enforce_tree_schema(
        table,
        city="San Francisco",
        data_source="SF_OPENDATA",
        columns={
            "tree_id": "treeid",
            "species": "qspecies",
            "plant_date": "plantdate",
            "diameter_at_breast_height": "dbh",
        },
    )
    emit(table)

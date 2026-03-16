#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests"]
# ///

import re
import sys
import io
import requests
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pv

DATASET_ID = "hn5i-inap"
DATASET_URL = (
    f"https://data.cityofnewyork.us/api/views/{DATASET_ID}/rows.csv?accessType=DOWNLOAD"
)

# Socrata exports point columns as WKT: "POINT (lon lat)"
_POINT_RE = re.compile(r"POINT\s*\(\s*([+-]?\d+\.?\d*)\s+([+-]?\d+\.?\d*)\s*\)")


def download_csv() -> io.BytesIO:
    r = requests.get(DATASET_URL, stream=True)
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
    # Parse WKT location point into latitude/longitude
    # Socrata CSV exports the point column as "Location" (capital L)
    loc_col = next((c for c in table.schema.names if c.lower() == "location"), None)
    if loc_col is not None:
        lons, lats = parse_point_column(table, loc_col)
        table = table.append_column("longitude", lons)
        table = table.append_column("latitude", lats)
        table = table.remove_column(table.schema.get_field_index(loc_col))

    # Parse date columns: Socrata exports as ISO-8601 strings
    planted_col = next((c for c in table.schema.names if c.lower() == "planteddate"), None)
    if planted_col is not None:
        idx = table.schema.get_field_index(planted_col)
        date_str = pc.utf8_slice_codeunits(table[planted_col], 0, 10)
        ts = pc.strptime(date_str, format="%Y-%m-%d", unit="s")
        table = table.set_column(idx, planted_col, pc.cast(ts, pa.date32()))

    # objectid: prefix with "nyc-" for global uniqueness across cities
    id_col = next((c for c in table.schema.names if c.lower() == "objectid"), None)
    if id_col is not None:
        ids = table[id_col].to_pylist()
        prefixed = pa.array(
            [f"nyc-{v}" if v is not None else None for v in ids],
            type=pa.string(),
        )
        table = table.set_column(table.schema.get_field_index(id_col), id_col, prefixed)

    # dbh: ensure int64
    dbh_col = next((c for c in table.schema.names if c.lower() == "dbh"), None)
    if dbh_col is not None:
        idx = table.schema.get_field_index(dbh_col)
        table = table.set_column(idx, dbh_col, pc.cast(table[dbh_col], pa.int64()))

    # Normalize all column names to lowercase to match preql mappings
    table = table.rename_columns([c.lower() for c in table.schema.names])

    return table


def load_arrow_table(csv_bytes: io.BytesIO) -> pa.Table:
    table = pv.read_csv(
        csv_bytes,
        convert_options=pv.ConvertOptions(
            strings_can_be_null=True,
            # Keep date/numeric columns as strings for manual casting
            column_types={
                "planteddate": pa.string(),
                "objectid": pa.string(),
                "dbh": pa.string(),
            },
        ),
    )
    return cast_columns(table)


def add_city_column(table: pa.Table) -> pa.Table:
    return table.append_column(
        "city", pa.array(["USNYC"] * table.num_rows, type=pa.string())
    )


def emit(table: pa.Table) -> None:
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == "__main__":
    csv_bytes = download_csv()
    table = load_arrow_table(csv_bytes)
    table = add_city_column(table)
    emit(table)

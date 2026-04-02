#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

import sys
import io
import requests
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pv
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit, normalize_species

DATASET_URL = (
    "https://data.boston.gov/dataset/e4c76e72-dcf1-40a0-b426-97c52214a9fe"
    "/resource/995cd80f-2489-41bf-b16b-113dba4f2797/download/bprd_trees.csv"
)


def download_csv() -> io.BytesIO:
    r = requests.get(DATASET_URL, stream=True)
    r.raise_for_status()

    buf = io.BytesIO()
    for chunk in r.iter_content(chunk_size=1024 * 1024):
        if chunk:
            buf.write(chunk)
    buf.seek(0)
    return buf


def cast_columns(table: pa.Table) -> pa.Table:
    # id: prefix with "bos-" for global uniqueness across cities
    id_col = next((c for c in table.schema.names if c.lower() == "id"), None)
    if id_col is not None:
        ids = table[id_col].to_pylist()
        prefixed = pa.array(
            [f"bos-{v}" if v is not None else None for v in ids],
            type=pa.string(),
        )
        table = table.set_column(table.schema.get_field_index(id_col), id_col, prefixed)

    # date_plant: try ISO-8601 date format (YYYY-MM-DD), null on failure
    date_col = next((c for c in table.schema.names if c.lower() == "date_plant"), None)
    if date_col is not None:
        idx = table.schema.get_field_index(date_col)
        raw = table[date_col].to_pylist()
        parsed = []
        for v in raw:
            if v is None or not isinstance(v, str) or len(v) < 10:
                parsed.append(None)
            else:
                try:
                    from datetime import date
                    parsed.append(date.fromisoformat(v[:10]))
                except ValueError:
                    parsed.append(None)
        table = table.set_column(idx, date_col, pa.array(parsed, type=pa.date32()))

    # dbh: ensure int64
    dbh_col = next((c for c in table.schema.names if c.lower() == "dbh"), None)
    if dbh_col is not None:
        idx = table.schema.get_field_index(dbh_col)
        as_float = pc.cast(table[dbh_col], pa.float64())
        table = table.set_column(idx, dbh_col, pc.cast(as_float, pa.int64(), safe=False))

    # spp_com: common name in "Genus, Qualifier" order — invert to "Qualifier Genus"
    spp_com_col = next((c for c in table.schema.names if c.lower() == "spp_com"), None)
    if spp_com_col is not None:
        def invert_common_name(v: str | None) -> str | None:
            if not v or not v.strip():
                return None
            parts = [p.strip() for p in v.split(",", 1)]
            return f"{parts[1]} {parts[0]}" if len(parts) == 2 and parts[1] else parts[0]
        tree_name = pa.array(
            [invert_common_name(v) for v in table[spp_com_col].to_pylist()],
            type=pa.string(),
        )
    else:
        tree_name = pa.array([None] * table.num_rows, type=pa.string())

    # Normalize spp_bot (scientific name) casing and use as fallback for tree_name
    spp_bot_col = next((c for c in table.schema.names if c.lower() == "spp_bot"), None)
    if spp_bot_col is not None:
        idx = table.schema.get_field_index(spp_bot_col)
        table = table.set_column(
            idx, spp_bot_col,
            pa.array([normalize_species(v) for v in table[spp_bot_col].to_pylist()], type=pa.string()),
        )
    spp_bot_list = table[spp_bot_col].to_pylist() if spp_bot_col else [None] * table.num_rows
    tree_name = pa.array(
        [t or s for t, s in zip(tree_name.to_pylist(), spp_bot_list)],
        type=pa.string(),
    )
    table = table.append_column("tree_name", tree_name)

    # Normalize all column names to lowercase
    table = table.rename_columns([c.lower() for c in table.schema.names])

    return table


def load_arrow_table(csv_bytes: io.BytesIO) -> pa.Table:
    table = pv.read_csv(
        csv_bytes,
        convert_options=pv.ConvertOptions(
            strings_can_be_null=True,
            column_types={
                "id": pa.string(),
                "spp_com": pa.string(),
                "date_plant": pa.string(),
                "dbh": pa.string(),
            },
        ),
    )
    return cast_columns(table)


def add_city_column(table: pa.Table) -> pa.Table:
    table = table.append_column(
        "city", pa.array(["USBOS"] * table.num_rows, type=pa.string())
    )
    return table.append_column(
        "usbos_source", pa.array(["CITY"] * table.num_rows, type=pa.string())
    )


if __name__ == "__main__":
    csv_bytes = download_csv()
    table = load_arrow_table(csv_bytes)
    table = add_city_column(table)
    emit(table)

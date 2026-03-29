#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests"]
# ///

import sys
import io
import requests
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pv

DATASET_ID = "tkzw-k3nq"
DATASET_URL = (
    f"https://data.sfgov.org/api/views/{DATASET_ID}/rows.csv?accessType=DOWNLOAD"
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


def normalize_species(s: str | None) -> str | None:
    if not s or not s.strip():
        return None
    parts = s.strip().split()
    return " ".join([parts[0].capitalize()] + [p.lower() for p in parts[1:]])


def cast_columns(table: pa.Table) -> pa.Table:
    # TreeID: prefix with "sf-" for global uniqueness across cities
    if "TreeID" in table.schema.names:
        ids = table["TreeID"].to_pylist()
        prefixed = pa.array(
            [f"sf-{v}" if v is not None else None for v in ids],
            type=pa.string(),
        )
        table = table.set_column(
            table.schema.get_field_index("TreeID"), "TreeID", prefixed
        )

    # PlantDate: "03/08/2024 12:00:00 AM" -> date32
    # pc.strptime doesn't support %p (AM/PM), so slice the date portion first
    if "PlantDate" in table.schema.names:
        date_str = pc.utf8_slice_codeunits(table["PlantDate"], 0, 10)  # "03/08/2024"
        ts = pc.strptime(date_str, format="%m/%d/%Y", unit="s")
        table = table.set_column(
            table.schema.get_field_index("PlantDate"),
            "PlantDate",
            pc.cast(ts, pa.date32()),
        )
    # SiteOrder: string -> int64
    if "SiteOrder" in table.schema.names:
        table = table.set_column(
            table.schema.get_field_index("SiteOrder"),
            "SiteOrder",
            pc.cast(table["SiteOrder"], pa.int64()),
        )
    # qSpecies: split "scientific :: common name" — store each part separately
    if "qSpecies" in table.schema.names:
        species_list = table["qSpecies"].to_pylist()
        scientific = pa.array(
            [normalize_species(v.split("::")[0]) if v is not None else None for v in species_list],
            type=pa.string(),
        )
        sci_list = scientific.to_pylist()
        tree_name = pa.array(
            [
                (v.split("::", 1)[1].strip() if v is not None and "::" in v else None) or s
                for v, s in zip(species_list, sci_list)
            ],
            type=pa.string(),
        )
        table = table.set_column(
            table.schema.get_field_index("qSpecies"), "qSpecies", scientific
        )
        table = table.append_column("tree_name", tree_name)
    return table


def load_arrow_table(csv_bytes: io.BytesIO) -> pa.Table:
    table = pv.read_csv(
        csv_bytes,
        convert_options=pv.ConvertOptions(
            strings_can_be_null=True,
            # Keep PlantDate and SiteOrder as strings so we can cast them ourselves
            column_types={"PlantDate": pa.string(), "SiteOrder": pa.string()},
        ),
    )
    # Filter to trees only before any further processing
    if "PlantType" in table.schema.names:
        table = table.filter(pc.equal(table["PlantType"], "Tree"))
    return cast_columns(table)


def add_city_column(table: pa.Table) -> pa.Table:
    return table.append_column(
        "city", pa.array(["USSFO"] * table.num_rows, type=pa.string())
    )


def emit(table: pa.Table) -> None:
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == "__main__":
    csv_bytes = download_csv()
    table = load_arrow_table(csv_bytes)
    table = add_city_column(table)
    emit(table)

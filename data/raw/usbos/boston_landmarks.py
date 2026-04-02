#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

import sys
import io
import requests
import pyarrow as pa
import pyarrow.csv as pv
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit

RESOURCE_ID = "fb53d967-ead6-4b4e-ab17-506521434038"
DATASET_URL = (
    f"https://data.boston.gov/dataset/92137315-e846-4c75-8c3d-2b7e93e38d03"
    f"/resource/{RESOURCE_ID}/download/boston_landmarks_commission_blc_landmarks.csv"
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
    # Unique_ID is stored as a float string (e.g. "132.000000000000000").
    # Convert to int and prefix with "bos-" for global uniqueness across cities.
    idx = table.schema.get_field_index("Unique_ID")
    if idx >= 0:
        ids = table["Unique_ID"].to_pylist()
        prefixed = pa.array(
            [f"bos-{int(float(v))}" if v is not None else None for v in ids],
            type=pa.string(),
        )
        table = table.set_column(idx, "Unique_ID", prefixed)

    # Date_Designated_1 is "M/D/YYYY H:MM:SS" — parse to date32.
    date_idx = table.schema.get_field_index("Date_Designated_1")
    if date_idx >= 0:
        raw = table["Date_Designated_1"].to_pylist()
        parsed = []
        for v in raw:
            if not isinstance(v, str) or not v.strip():
                parsed.append(None)
            else:
                try:
                    date_part = v.split()[0]
                    m, d, y = date_part.split("/")
                    parsed.append(date(int(y), int(m), int(d)))
                except (ValueError, IndexError):
                    parsed.append(None)
        table = table.set_column(date_idx, "Date_Designated_1", pa.array(parsed, type=pa.date32()))

    return table


def load_arrow_table(csv_bytes: io.BytesIO) -> pa.Table:
    table = pv.read_csv(
        csv_bytes,
        convert_options=pv.ConvertOptions(
            strings_can_be_null=True,
            column_types={
                "Unique_ID": pa.string(),
                "Date_Designated_1": pa.string(),
            },
        ),
    )
    return cast_columns(table)


def add_city_column(table: pa.Table) -> pa.Table:
    return table.append_column(
        "city", pa.array(["USBOS"] * table.num_rows, type=pa.string())
    )


if __name__ == "__main__":
    csv_bytes = download_csv()
    table = load_arrow_table(csv_bytes)
    table = add_city_column(table)
    emit(table)

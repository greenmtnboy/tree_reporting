#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests"]
# ///

import sys
import io
import requests
import pyarrow as pa
import pyarrow.csv as pv
from datetime import datetime, timezone

DATASET_ID = "rzic-39gi"
DATASET_URL = (
    f"https://data.sfgov.org/api/views/{DATASET_ID}/rows.csv?accessType=DOWNLOAD"
)
METADATA_URL = f"https://data.sfgov.org/api/views/{DATASET_ID}.json"


def fetch_rows_updated_at() -> datetime:
    r = requests.get(METADATA_URL)
    r.raise_for_status()
    meta = r.json()

    ts = meta.get("rowsUpdatedAt")
    if ts is None:
        raise RuntimeError("Dataset metadata missing rowsUpdatedAt")

    return datetime.fromtimestamp(ts, tz=timezone.utc)


def download_csv() -> io.BytesIO:
    r = requests.get(DATASET_URL, stream=True)
    r.raise_for_status()

    buf = io.BytesIO()
    for chunk in r.iter_content(chunk_size=1024 * 1024):
        if chunk:
            buf.write(chunk)
    buf.seek(0)
    return buf


def load_arrow_table(csv_bytes: io.BytesIO) -> pa.Table:
    return pv.read_csv(
        csv_bytes,
        convert_options=pv.ConvertOptions(strings_can_be_null=True),
    )


def add_rows_updated_at_column(table: pa.Table, updated_at: datetime) -> pa.Table:
    n = table.num_rows

    ts_array = pa.array(
        [updated_at] * n,
        type=pa.timestamp("us", tz="UTC"),
    )

    return table.append_column("rows_updated_at", ts_array)


def emit(table: pa.Table) -> None:
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == "__main__":
    rows_updated_at = fetch_rows_updated_at()
    csv_bytes = download_csv()
    table = load_arrow_table(csv_bytes)
    table = add_rows_updated_at_column(table, rows_updated_at)
    emit(table)

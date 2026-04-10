#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import requests

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit

RESOURCE_ID = "fb53d967-ead6-4b4e-ab17-506521434038"
METADATA_URL = f"https://data.boston.gov/api/3/action/resource_show?id={RESOURCE_ID}"


def fetch_rows_updated_at() -> datetime:
    r = requests.get(METADATA_URL, timeout=30)
    r.raise_for_status()
    meta = r.json()

    result = meta.get("result", {})
    ts = result.get("last_modified") or result.get("created")
    if ts is None:
        raise RuntimeError("Dataset metadata missing last_modified")

    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    emit(
        pa.table(
            {
                "city": pa.array(["USBOS"], type=pa.string()),
                "data_updated_through": pa.array(
                    [fetch_rows_updated_at()], type=pa.timestamp("us", tz="UTC")
                ),
            }
        )
    )

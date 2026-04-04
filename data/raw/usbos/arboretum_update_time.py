#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

import sys
from pathlib import Path
import pyarrow as pa
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import get_with_retry

# ArcGIS statistics query — fetches MAX(SDE_DT) without downloading the full dataset
STATS_URL = (
    "https://gis.arboretum.harvard.edu/arcgis/rest/services/Maps/Explorer/MapServer/34/query"
    "?where=1%3D1"
    "&outStatistics=%5B%7B%22statisticType%22%3A%22max%22%2C%22onStatisticField%22%3A%22SDE_DT%22%2C%22outStatisticFieldName%22%3A%22max_sde_dt%22%7D%5D"
    "&f=json"
)


def fetch_modified_at() -> datetime:
    r = get_with_retry(STATS_URL)
    data = r.json()

    features = data.get("features", [])
    if not features:
        raise RuntimeError("No features returned from ArcGIS statistics query")

    raw = features[0].get("attributes", {}).get("max_sde_dt")
    if raw is None:
        raise RuntimeError("max_sde_dt missing from statistics response")

    # ArcGIS returns dates as Unix milliseconds
    return datetime.fromtimestamp(raw / 1000, tz=timezone.utc)


def emit(updated_at: datetime) -> None:
    table = pa.table(
        {
            "city": pa.array(["USBOS"], type=pa.string()),
            "usbos_source": pa.array(["ARBORETUM"], type=pa.string()),
            "data_updated_through": pa.array([updated_at], type=pa.timestamp("us", tz="UTC")),
        }
    )
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == "__main__":
    emit(fetch_modified_at())

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

"""
Freshness probe for Berlin landmarks.

Runs a tiny Overpass query and reads `osm3s.timestamp_osm_base` from the
response metadata. This is the snapshot timestamp of the OSM database behind
the Overpass instance, and is dramatically cheaper than scanning all matching
landmarks just to compute a max element timestamp.
"""

import sys
from pathlib import Path
from datetime import datetime, timezone
import pyarrow as pa
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit, post_with_retry

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

QUERY = """
[out:json][timeout:25];
node(52.52,13.40,52.5201,13.4001);
out ids;
"""


def fetch_modified_at() -> datetime:
    r = post_with_retry(OVERPASS_URL, data={"data": QUERY}, timeout=90)
    data = r.json()
    ts = data.get("osm3s", {}).get("timestamp_osm_base")
    if ts is None:
        return datetime.now(tz=timezone.utc)
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


if __name__ == "__main__":
    emit(
        pa.table(
            {
                "city": pa.array(["DEBER"], type=pa.string()),
                "data_updated_through": pa.array(
                    [fetch_modified_at()], type=pa.timestamp("us", tz="UTC")
                ),
            }
        )
    )

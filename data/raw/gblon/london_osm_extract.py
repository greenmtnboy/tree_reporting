#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["google-cloud-storage", "pyarrow", "pytrilogy", "requests"]
# ///

"""Extract London's OpenStreetMap trees into the staged parquet in GCS.

Everything lives in `_osm_shared.extract_city`; this file exists so each city
has a discoverable entry point, and so a city that needs to diverge (a tighter
bbox, an extra tag) has somewhere to do it.

    cd data/raw && uv run gblon/london_osm_extract.py
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _osm_shared import extract_city  # noqa: E402

CITY_CODE = "GBLON"
CITY_NAME = "London OSM"

# London's municipal and community partitions both declare `borough`, so this
# one must too -- a partition that cannot supply a requested column drops out
# of the union and the rest stop covering the source enum. OSM has no borough,
# so it is all-null.
EXTRA_NULL_COLUMNS = {"borough": pa.string()}


if __name__ == "__main__":
    extract_city(CITY_CODE, CITY_NAME, EXTRA_NULL_COLUMNS)

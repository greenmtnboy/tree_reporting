#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["google-cloud-storage", "pyarrow", "pytrilogy", "requests"]
# ///

"""Extract Tokyo's OpenStreetMap trees into the staged parquet in GCS.

Everything lives in `_osm_shared.extract_city`; this file exists so each city
has a discoverable entry point and so a city that needs to diverge (a tighter
bbox, an extra tag) has somewhere to do it.

    cd data/raw && uv run jptyo/tokyo_osm_extract.py

The scheduled `osm-jptyo` [[cloud.job]] is the normal path; this is the manual
counterpart, for bootstrapping the city before its job is deployed.  Both share
`fetch_osm_trees` / `build_table`, so they cannot drift on content -- only on
who writes the GCS object.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _osm_shared import extract_city  # noqa: E402

CITY_CODE = "JPTYO"
CITY_NAME = "Tokyo OSM"

# Tokyo's municipal and community partitions both declare `borough` -- the
# ward, `行政区` -- so this one must too: a partition that cannot supply a
# requested column drops out of the union and the rest stop covering the
# source enum.  OSM has no ward, so it is all-null.
EXTRA_NULL_COLUMNS = {"borough": pa.string()}


if __name__ == "__main__":
    extract_city(CITY_CODE, CITY_NAME, EXTRA_NULL_COLUMNS)

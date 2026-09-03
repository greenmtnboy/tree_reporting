#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["google-cloud-storage", "pyarrow", "pytrilogy", "requests"]
# ///

"""Extract Berlin's OpenStreetMap trees into the staged parquet in GCS.

Everything lives in `_osm_shared.extract_city`; this file exists so each city
has a discoverable entry point, and so a city that needs to diverge (a tighter
bbox, an extra tag) has somewhere to do it.

    cd data/raw && uv run deber/berlin_osm_extract.py

The scheduled `osm-deber` [[cloud.job]] is the normal path (see
../trilogy.toml and osm_staging/deber_osm_staging.preql); this script is the
manual counterpart, for bootstrapping a city before its job is deployed or
re-extracting from a workstation. Both share `_osm_shared.fetch_osm_trees` /
`build_table`, so they cannot differ on content -- only on who writes the GCS
object.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _osm_shared import extract_city  # noqa: E402

CITY_CODE = "DEBER"
CITY_NAME = "Berlin OSM"


if __name__ == "__main__":
    extract_city(CITY_CODE, CITY_NAME)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["google-cloud-storage", "pyarrow", "pytrilogy", "requests"]
# ///

"""Extract Longueuil's OpenStreetMap trees into the staged parquet in GCS.

Everything lives in `_osm_shared.extract_city`; this file exists so each city
has a discoverable entry point and so a city that needs to diverge (a tighter
bbox, an extra tag) has somewhere to do it.

    cd data/raw && uv run calon/longueuil_osm_extract.py

The scheduled `osm-calon` [[cloud.job]] is the normal path; this is the manual
counterpart, for bootstrapping the city before its job is deployed.  Both share
`fetch_osm_trees` / `build_table`, so they cannot drift on content -- only on
who writes the GCS object.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _osm_shared import extract_city  # noqa: E402

CITY_CODE = "CALON"
CITY_NAME = "Longueuil OSM"


if __name__ == "__main__":
    extract_city(CITY_CODE, CITY_NAME)

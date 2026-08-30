#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["google-cloud-storage", "pyarrow", "pytrilogy", "requests"]
# ///

"""Extract Santorini's OpenStreetMap trees into the staged parquet in GCS.

The normal path is the scheduled `osm-grsan` cloud job, which refreshes
osm_staging/grsan_osm_staging.preql on its own cron with the pipeline's GCS
credentials — this script is the manual fallback, and it needs
application-default Google credentials for the upload.

Everything lives in `_osm_shared.extract_city`; this file exists so each city
has a discoverable entry point, and so a city that needs to diverge (a tighter
bbox, an extra tag) has somewhere to do it.

Small extract by design: the caldera group held ~155 natural=tree nodes when
wired (counted via the ohsome API), on the same order as Milos's ~240.
`fetch_osm_trees` still raises on a truly empty result, which for Santorini
would mean the nodes were deleted or the bbox drifted — either way worth a
look rather than an empty publish.

    cd data/raw && uv run grsan/santorini_osm_extract.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _osm_shared import extract_city  # noqa: E402

CITY_CODE = "GRSAN"
CITY_NAME = "Santorini OSM"


if __name__ == "__main__":
    extract_city(CITY_CODE, CITY_NAME)

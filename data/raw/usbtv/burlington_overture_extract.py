#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["duckdb", "google-cloud-storage", "pyarrow", "pytrilogy", "requests"]
# ///

"""Build Burlington's Overture position lookup and publish it to GCS.

Everything lives in `shared.overture.extract_city_snap`; this file exists so
the city has a discoverable entry point, the same way its OSM extract does.

    cd data/raw && uv run usbtv/burlington_overture_extract.py

The scheduled `overture-usbtv` [[cloud.job]] is the normal path (see
../trilogy.toml and overture_staging/usbtv_overture_staging.preql); this
script is the manual counterpart, for staging the table before the job is
deployed or rebuilding it from a workstation.  Both share
`shared.overture.build_snap_table`, so they cannot differ on content -- only
on who writes the GCS object.  Pin a release with `OVERTURE_RELEASE=...`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.overture import extract_city_snap  # noqa: E402

CITY_CODE = "USBTV"


if __name__ == "__main__":
    extract_city_snap(CITY_CODE)

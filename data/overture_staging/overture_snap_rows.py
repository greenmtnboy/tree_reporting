#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["duckdb", "pyarrow", "pytrilogy", "requests"]
# ///
"""Every city's Overture position lookup, as one datasource script for the
scheduled staging jobs.

Which city it builds comes from the model, not from a per-city copy of this
file: each `{code}_overture_staging.preql` declares

    root datasource {code}_overture_snap_rows (...)
    file `./overture_snap_rows.py`
    where city = '{CODE}';

and Trilogy compiles that `where` into both a SQL predicate and a
`--filter 'city={CODE}'` argument here.

**The filter is mandatory here**, as it is for the OSM extracts: it decides
what is *built*, and building every city's table on every firing would read
the whole of Overture's buildings and roads for forty bounding boxes to
throw thirty-nine of them away.  A missing or unknown city is a hard failure.

    cd data && trilogy refresh overture_staging/usbtv_overture_staging.preql

The build itself lives in raw/shared/overture.py, shared with the manual
`raw/{code}/{slug}_overture_extract.py` path, so the two cannot drift.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "raw"))
from shared.ingest import parse_pushdown_filters  # noqa: E402
from shared.overture import OVERTURE_SNAP_CITIES, stage_city_snap  # noqa: E402


def main(argv: list[str] | None = None) -> None:
    filters = parse_pushdown_filters(list(sys.argv[1:] if argv is None else argv))
    code = (filters.get("city") or "").upper()
    if not code:
        raise SystemExit(
            "overture_snap_rows.py needs a city: the staging model's datasource "
            "must carry `where city = '<CODE>'`, which Trilogy pushes down as "
            "--filter. Without it this script would build every city on every "
            "firing."
        )
    if code not in OVERTURE_SNAP_CITIES:
        raise SystemExit(
            f"{code} has no Overture position lookup configured; add it to "
            "OVERTURE_SNAP_CITIES in raw/shared/overture.py first"
        )
    stage_city_snap(code)


if __name__ == "__main__":
    main()

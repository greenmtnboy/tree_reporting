#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["duckdb", "pyarrow", "pytrilogy", "requests"]
# ///
"""Every city's LIDAR canopy-height lookup, as one datasource script.

Each `{code}_canopy_staging.preql` declares this file with
`where city = '{CODE}'`, which Trilogy pushes down as `--filter city={CODE}`;
that is how the script knows which city to build, and it refuses to run
without one.  The build lives in raw/shared/canopy.py.

    cd data && trilogy refresh canopy_staging/usbos_canopy_staging.preql
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "raw"))
from shared.canopy import CANOPY_LAYERS, stage_city_height  # noqa: E402
from shared.ingest import parse_pushdown_filters  # noqa: E402


def main(argv: list[str] | None = None) -> None:
    filters = parse_pushdown_filters(list(sys.argv[1:] if argv is None else argv))
    code = (filters.get("city") or "").upper()
    if not code:
        raise SystemExit(
            "canopy_height_rows.py needs a city: the staging model's datasource "
            "must carry `where city = '<CODE>'`, which Trilogy pushes down as --filter."
        )
    if code not in CANOPY_LAYERS:
        raise SystemExit(f"{code} has no canopy survey configured; add it to CANOPY_LAYERS in raw/shared/canopy.py")
    stage_city_height(code)


if __name__ == "__main__":
    main()

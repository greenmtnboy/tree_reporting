#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Every city's OSM trees, as one datasource script for the scheduled extracts.

Which city it fetches comes from the model, not from a per-city copy of this
file: each `{code}_osm_staging.preql` declares

    root datasource {code}_osm_rows (...)
    file `./osm_rows.py`
    where city = '{CODE}';

and Trilogy compiles that `where` into both a SQL predicate and a
`--filter 'city={CODE}'` argument here.  That replaced seventeen eight-line
shims that differed only in a string.

**The filter is mandatory here, unlike everywhere else it is used.**  For
`community_tree_info.py` a pushdown filter is an optimisation — that script
reads a static export, and the SQL predicate applies the same restriction one
layer up if the argument never arrives.  Here it decides *what we fetch*: no
filter would mean querying Overpass for all seventeen bounding boxes on every
firing, seventeen times the load on an API that allows two concurrent slots per
IP, and the SQL predicate would then throw sixteen seventeenths of it away.  So
a missing or unknown city is a hard failure, and the extract does not run.

    cd data && trilogy refresh osm_staging/ussfo_osm_staging.preql

The extraction itself lives in raw/_osm_shared.py, shared with the manual
`raw/{code}/{city}_osm_extract.py` path, so the two cannot drift on content.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "raw"))
from _ingest_shared import parse_pushdown_filters  # noqa: E402
from _osm_shared import OSM_CITY_NAMES, stage_city_rows  # noqa: E402


def main(argv: list[str] | None = None) -> None:
    filters = parse_pushdown_filters(list(sys.argv[1:] if argv is None else argv))
    code = (filters.get("city") or "").upper()
    if not code:
        raise SystemExit(
            "osm_rows.py needs a city: the staging model's datasource must "
            "carry `where city = '<CODE>'`, which Trilogy pushes down as "
            "--filter. Without it this script would query Overpass for every "
            "city on every firing."
        )
    if code not in OSM_CITY_NAMES:
        raise SystemExit(
            f"{code} has no OSM extract configured; add it to OSM_DATA_SOURCES "
            "and CITY_BOUNDS in raw/_ingest_shared.py first"
        )
    stage_city_rows(code)


if __name__ == "__main__":
    main()

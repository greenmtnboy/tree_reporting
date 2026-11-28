#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""East Lansing, Michigan's National Register listings.

**The National Register of Historic Places** (the National Park Service's
`cultural_resources/nrhp_locations` MapServer, points then polygons), read
live, within `CITY_BOUNDS["USELN"]` and with `City = East Lansing` -- the box also
holds listings in the places around it.  Listed buildings, sites and
structures, keyed `eln-nr-{NRIS_Refnum}` with `CertDate` as
`year_designated`; districts are areas and are not published.  NPS data is a
US federal work.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit  # noqa: E402
from shared.nrhp import national_register_table  # noqa: E402

if __name__ == "__main__":
    emit(national_register_table("USELN", "East Lansing", "eln"))

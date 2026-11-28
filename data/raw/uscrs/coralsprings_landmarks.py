#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Coral Springs, Florida's National Register listings.

**The National Register of Historic Places** (the National Park Service's
`cultural_resources/nrhp_locations` MapServer, points then polygons), read
live, within `CITY_BOUNDS["USCRS"]` and with `City = Coral Springs`.  Listed
buildings, sites and structures, keyed `crs-nr-{NRIS_Refnum}` with
`CertDate` as `year_designated`; districts are areas and are not published.
NPS data is a US federal work.

**The register has no listing in Coral Springs** (2026-11-27: no point or
polygon inside the box at all; the city was founded in 1963), and the City
publishes no historic-landmark register of its own (its ArcGIS Online
organisation `mXWZ2wsX9klsJAJY` has none), so the table is empty today and
the job publishes it rather than failing.  A listing added later appears on
the next weekly run.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit  # noqa: E402
from shared.nrhp import national_register_table  # noqa: E402

if __name__ == "__main__":
    emit(national_register_table("USCRS", "Coral Springs", "crs", required=False))

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""St. Louis Park, Minnesota's National Register listings.

**The National Register of Historic Places** (the National Park Service's
`cultural_resources/nrhp_locations` MapServer, points then polygons), read
live, within `CITY_BOUNDS["USSLP"]` and with `City = St. Louis Park` -- the
box also holds listings in Minneapolis, Edina, Golden Valley and Hopkins.
Listed buildings, sites and structures, keyed `slp-nr-{NRIS_Refnum}` with
`CertDate` as `year_designated`; districts are areas and are not published.
Two listings when the city was wired (2026-11-27): the Chicago, Milwaukee,
St. Paul and Pacific Depot and the Peavey-Haglin Experimental Concrete Grain
Elevator.  NPS data is a US federal work.

The City publishes no landmark register of its own: its ArcGIS organisation
has only the Historic Walker Lake District zoning overlay, an area.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit  # noqa: E402
from shared.nrhp import national_register_table  # noqa: E402

if __name__ == "__main__":
    emit(national_register_table("USSLP", "St. Louis Park", "slp"))

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""San Mateo, California's National Register of Historic Places listings.

**The National Register of Historic Places** (the National Park Service's
`cultural_resources/nrhp_locations` MapServer, points then polygons) within
`CITY_BOUNDS["USSMQ"]` and with `City = San Mateo` -- the box also holds
Burlingame, Hillsborough, Belmont and San Carlos.  Listed buildings, sites
and structures, keyed `sanmateo-nr-{NRIS_Refnum}` with `CertDate` as
`year_designated`; districts (the De Sabla Teahouse and Tea Garden, the
Yamanouchi House) are areas and are not published (Durham's rule).  NPS data
is a US federal work.  6 listings on 2026-11-27.

San Mateo publishes no designation register of its own.  The City's
`Historic_Resources` layer (ArcGIS item 3016735dab834195a97f0e27e7ab9cb9,
owner `pworks_forsanmateo`, last edited 2022-06-24, no licence stated) is
204 parcel polygons carrying a survey eligibility ranking
(`HISTORIC_RANKING`: "(3) National Register - Individually Eligible",
"(6) Ineligible ...") and an APN, with no name or address: an eligibility
survey of parcels, not a list of designated landmarks.  It is not read.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit  # noqa: E402
from shared.nrhp import national_register_table  # noqa: E402

if __name__ == "__main__":
    emit(national_register_table("USSMQ", "San Mateo", "sanmateo"))

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Bismarck, ND's National Register of Historic Places listings.

**The National Register of Historic Places** (the National Park Service's
`cultural_resources/nrhp_locations` MapServer, points then polygons) within
`CITY_BOUNDS["USBIS"]` and with `City = Bismarck`.  Listed buildings, sites
and structures, keyed `bis-nr-{NRIS_Refnum}` with `CertDate` as
`year_designated`; districts (Downtown, Cathedral Area, Highland Acres and
their boundary increases) are areas and are not published (Durham's rule).
NPS data is a US federal work.  20 listings on 2026-11-22.

Bismarck keeps no local landmark register of its own: the City's
"Historic Ditstrict Properties" layer (ArcGIS item ab4f84e44fa64235b927dd2a5e0350eb,
titled "NRHP Properties" in the City's own web map) is a copy of these same
National Register properties with no reference numbers and no licence, and
its `Historic_Districts` layer holds the districts.  Neither is read.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit  # noqa: E402
from shared.nrhp import national_register_table  # noqa: E402

if __name__ == "__main__":
    emit(national_register_table("USBIS", "Bismarck", "bis"))

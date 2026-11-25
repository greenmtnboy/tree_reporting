#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Everett, Massachusetts's National Register listings.

**The National Register of Historic Places** (the National Park Service's
`cultural_resources/nrhp_locations` MapServer, points then polygons), read
live, within `CITY_BOUNDS["USEVE"]` and with `City = Everett` -- the box also
holds listings in the towns around it.  Listed buildings, sites and
structures, keyed `eve-nr-{NRIS_Refnum}` with `CertDate` as
`year_designated`; districts are areas and are not published.  NPS data is a
US federal work.

**The register has no listing in Everett** (2026-11-25: the listings inside
its box are all Chelsea's and Malden's), so the table may be empty and the
job publishes it rather than failing.

Everett had no landmarks of its own while its trees were a partition of
Boston's metro model: Boston's landmark file is the Boston Landmarks
Commission's, which stops at the city line.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit  # noqa: E402
from shared.nrhp import national_register_table  # noqa: E402

if __name__ == "__main__":
    emit(national_register_table("USEVE", "Everett", "eve", required=False))

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Plano, TX's National Register of Historic Places listings.

**The National Register of Historic Places** (the National Park Service's
`cultural_resources/nrhp_locations` MapServer, points then polygons) within
`CITY_BOUNDS["USPLN"]` and with `City = Plano`.  Listed buildings, sites
and structures, keyed `pln-nr-{NRIS_Refnum}` with `CertDate` as
`year_designated`; districts are areas and are not published (Durham's
rule).  NPS data is a US federal work.  Three listings on 2026-11-23 (the
Ammie Wilson House, the Saigling House, the Texas Pool); the box also holds
the Allen Water Station, filed under Allen.

The City's own heritage designations are not read.  Plano publishes no
register layer of its individually designated Heritage Resources: the only
layer that marks them (`HeritagePreservation/HeritageTaxExemptReference`
on maps.planogis.org, `LANDMARK_FLAG`) is the heritage tax-exemption
programme's enrolment list -- 23 enrolled landmarks, not the register, and
the same table carries owners' names and e-mail addresses -- and the City's
heritage items state no licence (the item text is a reliance disclaimer).
Its two heritage districts (Downtown, Haggard Park) are areas.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit  # noqa: E402
from shared.nrhp import national_register_table  # noqa: E402

if __name__ == "__main__":
    emit(national_register_table("USPLN", "Plano", "pln"))

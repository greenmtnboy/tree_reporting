#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Oxnard, CA's landmarks.

The newest NPS National Register `EDIT_DATE` over the rows inside
`CITY_BOUNDS["USOXR"]` (the whole box, not only the Oxnard rows, so a listing
whose `City` is corrected into Oxnard moves it too).

The City's landmark layer (`HistoricData/MapServer/0`) has no stamp to read:
a MapServer layer with no `editingInfo`, no date column and no ArcGIS Online
item of its own.  Its 26 County-numbered designations change perhaps once a
decade (the newest is 2015), so an edit there is picked up the next time the
National Register box moves or the lane is forced.  No stamp in the box
raises (a schema question); an outage degrades through `emit_freshness`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness  # noqa: E402
from shared.nrhp import national_register_modified_at  # noqa: E402

if __name__ == "__main__":
    emit_freshness("USOXR", lambda: national_register_modified_at("USOXR", "Oxnard"), label="USOXR landmarks")

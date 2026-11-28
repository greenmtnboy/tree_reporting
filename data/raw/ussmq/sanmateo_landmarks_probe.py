#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for San Mateo, California's landmarks.

The newest NPS National Register `EDIT_DATE` over the rows inside
`CITY_BOUNDS["USSMQ"]` (the whole box, not only the San Mateo rows, so a
listing whose `City` is corrected into San Mateo moves it too), so the weekly
landmark lane only rebuilds San Mateo's parquet when a listing has changed.
No stamp in the box raises (a schema question); an outage degrades through
`emit_freshness`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness  # noqa: E402
from shared.nrhp import national_register_modified_at  # noqa: E402

if __name__ == "__main__":
    emit_freshness("USSMQ", lambda: national_register_modified_at("USSMQ", "San Mateo"), label="USSMQ landmarks")

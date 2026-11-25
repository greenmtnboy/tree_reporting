#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Dedham, Massachusetts's landmarks.

The newest `EDIT_DATE` over the NPS National Register rows inside
`CITY_BOUNDS["USDED"]` (the whole box, not only Dedham's rows, so a
listing whose `City` is corrected into Dedham moves it too), so the weekly
landmark lane only rebuilds Dedham's parquet when the register changed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness  # noqa: E402
from shared.nrhp import national_register_modified_at  # noqa: E402

if __name__ == "__main__":
    emit_freshness("USDED", lambda: national_register_modified_at("USDED", "Dedham"), label="USDED landmarks")

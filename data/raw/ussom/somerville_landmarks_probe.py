#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Somerville, Massachusetts's landmarks.

The newest `EDIT_DATE` over the NPS National Register rows inside
`CITY_BOUNDS["USSOM"]` (the whole box, not only Somerville's rows, so a
listing whose `City` is corrected into Somerville moves it too), so the weekly
landmark lane only rebuilds Somerville's parquet when the register changed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness  # noqa: E402
from shared.nrhp import national_register_modified_at  # noqa: E402

if __name__ == "__main__":
    emit_freshness("USSOM", lambda: national_register_modified_at("USSOM", "Somerville"), label="USSOM landmarks")

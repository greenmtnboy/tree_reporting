#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Moorhead, Minnesota's landmarks.

The newest `EDIT_DATE` over the NPS National Register rows inside
`CITY_BOUNDS["USMHD"]` (the whole box, Fargo's rows too, so a listing whose
`City` is corrected into Moorhead moves it), so the weekly landmark lane only
rebuilds Moorhead's parquet when the register has changed there.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness  # noqa: E402
from shared.nrhp import national_register_modified_at  # noqa: E402

if __name__ == "__main__":
    emit_freshness("USMHD", lambda: national_register_modified_at("USMHD", "Moorhead"), label="USMHD landmarks")

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Winthrop, Massachusetts's landmarks.

The newest `EDIT_DATE` over the NPS National Register rows inside
`CITY_BOUNDS["USWNT"]` (the whole box, not only Winthrop's rows, so a
listing whose `City` is corrected into Winthrop moves it too), so the weekly
landmark lane only rebuilds Winthrop's parquet when the register changed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness  # noqa: E402
from shared.nrhp import national_register_modified_at  # noqa: E402

if __name__ == "__main__":
    emit_freshness("USWNT", lambda: national_register_modified_at("USWNT", "Winthrop"), label="USWNT landmarks")

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Coral Springs, Florida's landmarks.

The newest `EDIT_DATE` over the NPS National Register rows inside
`CITY_BOUNDS["USCRS"]` (the whole box, not only Coral Springs's rows, so a
listing whose `City` is corrected into the city moves it too), so the weekly
landmark lane only rebuilds the parquet when the register changed.

The box holds no register row at all today (2026-11-27), where
`national_register_modified_at` would raise and end every refresh.  So an
empty box reports `WIRED_AT`, the day this probe was written: a listing
that appears later carries an `EDIT_DATE` after it, which moves the stamp.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness  # noqa: E402
from shared.nrhp import NRHP_LAYERS, bounds_query  # noqa: E402
from shared.platforms.arcgis import esri_ms_to_datetime, iter_features  # noqa: E402

WIRED_AT = datetime(2026, 11, 27, tzinfo=timezone.utc)


def fetch_modified_at() -> datetime:
    stamps = [
        esri_ms_to_datetime((feature.get("attributes") or {}).get("EDIT_DATE"))
        for layer in NRHP_LAYERS
        for page in iter_features(
            layer, out_fields="OBJECTID,EDIT_DATE", extra_params=bounds_query("USCRS")
        )
        for feature in page
    ]
    return max([s for s in stamps if s is not None], default=WIRED_AT)


if __name__ == "__main__":
    emit_freshness("USCRS", fetch_modified_at, label="USCRS landmarks")

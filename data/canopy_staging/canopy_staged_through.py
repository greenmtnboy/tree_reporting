#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for the canopy-height staging models: the newest hand-set
CANOPY_STAGED_THROUGH date (raw/shared/canopy.py).  City-agnostic, because a
freshness probe is not given the model's pushdown filter."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "raw"))
from shared.canopy import CANOPY_STAGED_THROUGH  # noqa: E402
from shared.ingest import emit_freshness  # noqa: E402

if __name__ == "__main__":
    emit_freshness(None, lambda: max(CANOPY_STAGED_THROUGH.values()), label="canopy surveys")

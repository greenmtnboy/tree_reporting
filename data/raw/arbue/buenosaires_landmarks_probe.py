#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy"]
# ///

"""
Freshness probe for Buenos Aires landmarks.
The landmark list is hand-curated in buenosaires_landmarks.py, so its
watermark is that module's UPDATED_AT constant: bump it when the list changes.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from buenosaires_landmarks import UPDATED_AT
from shared.ingest import emit_freshness


if __name__ == "__main__":
    emit_freshness("ARBUE", lambda: UPDATED_AT)

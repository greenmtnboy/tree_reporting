#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

from datetime import datetime, timezone
from pathlib import Path


import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness

CSV_PATH = Path(__file__).parent / 'tempe_landmarks.csv'


def modified_at() -> datetime:
    if not CSV_PATH.exists():
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return datetime.fromtimestamp(CSV_PATH.stat().st_mtime, tz=timezone.utc)


if __name__ == "__main__":
    emit_freshness("USTEM", modified_at)

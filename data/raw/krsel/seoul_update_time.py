#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Seoul's street trees (Seoul Open Data Plaza, OA-23040).

The watermark is the 수정일 (modified date) of the dataset's current data file,
read from the dataset page's file table by `current_data_file` -- the same row
the ingest downloads, so a new year's file or a re-posting of the current one
moves it.  The date is a calendar day in Korea Standard Time, emitted as
midnight KST in UTC.  The dataset is updated yearly (갱신주기 연간).

A maintenance or error page degrades through `emit_freshness`; a dataset page
with no xlsx data row or an unreadable date raises, because that is the portal
changing its layout.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
from shared.ingest import emit_freshness  # noqa: E402
from seoul_tree_info import current_data_file  # noqa: E402


def fetch_modified_at() -> datetime:
    return current_data_file()["modified"]


if __name__ == "__main__":
    emit_freshness("KRSEL", fetch_modified_at)

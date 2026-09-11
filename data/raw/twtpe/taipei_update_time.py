#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Taipei's street and park trees.

The two CSVs are static blobs on Azure storage with no catalogue stamp that
tracks them (`data.taipei`'s dataset page records when the *metadata* was
edited), so the watermark is the blobs' own `Last-Modified`: two HEAD
requests against a 19 MB pair of files.

**The later of the two**, because the ingest unions both and either can move
on its own -- the street file was republished on 2026-09-11 and the park file
on 2026-07-14 when this was written, two months apart.  Taking one would
freeze the city whenever the other was the thing that changed.

A blob with no `Last-Modified` raises rather than degrading: that is the
publisher changing how the files are served, not an outage, and
`emit_freshness` must not read it as "no new data".
"""

import sys
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness, head_with_retry

# Kept in step with `taipei_tree_info.py` by `tests/test_city_wiring.py`.
FILES = (
    "https://tppkl.blob.core.windows.net/blobfs/TaipeiTree.csv",
    "https://tppkl.blob.core.windows.net/blobfs/TaipeiParkTree.csv",
)


def blob_last_modified(url: str) -> datetime:
    response = head_with_retry(url, timeout=60)
    stamp = response.headers.get("Last-Modified")
    if not stamp:
        raise RuntimeError(f"no Last-Modified header on {url}")
    return parsedate_to_datetime(stamp)


def fetch_modified_at() -> datetime:
    return max(blob_last_modified(url) for url in FILES)


if __name__ == "__main__":
    emit_freshness("TWTPE", fetch_modified_at)

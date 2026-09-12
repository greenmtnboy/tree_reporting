#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Paris's protected monuments.

The register is a file in the Ministry of Culture's POP bucket, so the watermark
is its `Last-Modified`: one HEAD against a 100 MB CSV, which is the point of
having a probe at all.

The old probe read `metas.default.modified` from data.iledefrance.fr's catalogue.
That portal has closed the data API behind this dataset (403 on every export; see
`paris_landmarks.py`), and its metadata endpoint still answers 200 -- so the old
probe would have gone on reporting a healthy watermark for a source that could no
longer be read, which is the worst shape a probe can take.

A missing `Last-Modified` raises rather than degrading: the bucket always sends
one, so its absence means this URL is not the object we think it is. A transport
failure raises `UpstreamUnavailable`, which `emit_freshness` turns into the epoch
so Paris sits the tick out and the other forty cities refresh normally.
"""

import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness, head_with_retry

# Kept in step with `paris_landmarks.py` by `tests/test_city_wiring.py`.
REGISTER_URL = "https://ministere-culture.s3.sbg.io.cloud.ovh.net/POP/merimee.csv"


def fetch_last_modified() -> datetime:
    response = head_with_retry(REGISTER_URL, timeout=60)
    header = response.headers.get("Last-Modified")
    if not header:
        raise RuntimeError(f"no Last-Modified header on {REGISTER_URL}")
    return parsedate_to_datetime(header).astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("FRPAR", fetch_last_modified)

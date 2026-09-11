#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Taipei's monuments and historic buildings.

The bureau's JSON files are served without a `Last-Modified` header, so the
watermark is the national open-data portal's record of them: `modifiedDate`
from `data.gov.tw`'s dataset API, for each of the two datasets the script
unions, and the later of the two.  The portal stamps it when the bureau
republishes the file (both read 2026-09-07 when this was written), which is
what a weekly landmark lane wants to know.

A record without `modifiedDate` raises rather than degrading: that is the
portal changing its API, not an outage.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness, get_json_with_retry

DATASETS = (6246, 6965)  # 文資局古蹟, 文資局歷史建築
TAIWAN = timezone(timedelta(hours=8))


def dataset_modified_at(dataset_id: int) -> datetime:
    url = f"https://data.gov.tw/api/v2/rest/dataset/{dataset_id}"
    result = get_json_with_retry(url, timeout=60).get("result") or {}
    raw = result.get("modifiedDate")
    if not raw:
        raise RuntimeError(f"data.gov.tw dataset {dataset_id} carries no modifiedDate")
    # `2026-09-07 16:39:34`, Taiwan time.
    stamp = datetime.fromisoformat(str(raw).strip())
    return stamp.replace(tzinfo=TAIWAN) if stamp.tzinfo is None else stamp


def fetch_modified_at() -> datetime:
    return max(dataset_modified_at(dataset_id) for dataset_id in DATASETS)


if __name__ == "__main__":
    emit_freshness("TWTPE", fetch_modified_at, label="TWTPE landmarks")

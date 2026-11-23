#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Seoul's protected and veteran trees (OA-1323).

The register's own 데이터 갱신일 (data updated date) from the dataset page --
2022-10-14 when the city was wired -- as midnight Korea Standard Time in UTC.
Not the page's 메타정보 수정일 (metadata modified), which the portal moves for
catalogue edits and moved on the day this was written.

A page that is not the dataset page (maintenance, error) degrades through
`emit_freshness`; a dataset page without a readable 데이터 갱신일 raises.
"""

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import UpstreamUnavailable, emit_freshness, get_with_retry  # noqa: E402

DATASET_ID = "OA-1323"
DATASET_PAGE = f"https://data.seoul.go.kr/dataList/{DATASET_ID}/S/1/datasetView.do"
KST = timezone(timedelta(hours=9))
_UPDATED = re.compile(
    r"데이터 갱신일</th>\s*<td>\s*<span[^>]*>\s*(\d{4})\.(\d{1,2})\.(\d{1,2})", re.S
)


def fetch_modified_at() -> datetime:
    page = get_with_retry(DATASET_PAGE, timeout=60).text
    if f'value="{DATASET_ID}"' not in page:
        raise UpstreamUnavailable(f"{DATASET_PAGE} is not the {DATASET_ID} dataset page")
    match = _UPDATED.search(page)
    if match is None:
        raise RuntimeError(f"{DATASET_PAGE}: no readable 데이터 갱신일")
    return datetime(*map(int, match.groups()), tzinfo=KST).astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("KRSEL", fetch_modified_at, label="KRSEL landmarks")

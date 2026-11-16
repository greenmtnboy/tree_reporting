#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Ballarat's Exceptional Tree Register.

The register's OpenDataSoft catalogue record, `.metas.default.modified`, read
the same way as the inventory's probe (ballarat_update_time.py).  It read
2023-12-04T02:46:09Z on 2026-11-16.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

METADATA_URL = (
    "https://data.ballarat.vic.gov.au/api/explore/v2.1/catalog/datasets/"
    "exceptional-tree-register"
)


def fetch_modified_at() -> datetime:
    meta = get_json_with_retry(METADATA_URL, timeout=60)
    ts = ((meta.get("metas") or {}).get("default") or {}).get("modified")
    if not ts:
        raise RuntimeError("Ballarat exceptional tree register metadata missing metas.default.modified")
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("AUBAL", fetch_modified_at, label="AUBAL exceptional trees")

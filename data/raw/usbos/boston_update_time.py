#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness, get_json_with_retry

RESOURCE_ID = "995cd80f-2489-41bf-b16b-113dba4f2797"
METADATA_URL = f"https://data.boston.gov/api/3/action/resource_show?id={RESOURCE_ID}"


def fetch_rows_updated_at() -> datetime:
    meta = get_json_with_retry(METADATA_URL)

    result = meta.get("result", {})
    ts = result.get("last_modified") or result.get("created")
    if ts is None:
        raise RuntimeError("Dataset metadata missing last_modified")

    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


if __name__ == "__main__":
    emit_freshness("USBOS", fetch_rows_updated_at)

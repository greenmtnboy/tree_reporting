#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Basel's tree register.

Reads `metas.default.modified` from the OpenDataSoft catalogue record of
dataset 100052 -- one small JSON request.  The portal re-harvests the layer
daily (`data_processed` moves every day whether or not a tree changed), so
`modified`, which the canton stamps when the register itself changes, is the
watermark; it is a day-precision date.

A transport failure or a maintenance page degrades to the epoch through
`get_json_with_retry` / `emit_freshness`, so Basel sits out that run.  A
record without the field raises: the portal changed shape, and degrading
would freeze the city silently.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

METADATA_URL = "https://data.bs.ch/api/explore/v2.1/catalog/datasets/100052"


def fetch_modified_at() -> datetime:
    meta = get_json_with_retry(METADATA_URL)
    stamp = ((meta.get("metas") or {}).get("default") or {}).get("modified")
    if not stamp:
        raise RuntimeError("data.bs.ch dataset 100052: metas.default.modified is missing")
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("CHBAS", fetch_modified_at)

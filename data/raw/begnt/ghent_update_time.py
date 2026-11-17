#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Ghent's tree inventory.

Reads `metas.default.modified` from the OpenDataSoft catalogue record of
`locaties-bomen-gent` on data.stad.gent -- one small JSON request.  The portal
re-harvests the Groendienst export daily and stamps `modified` with each
harvest (it equals `data_processed`), so the watermark moves every day and
the city rebuilds on each scheduled tick; the export is ~3 MB, so that costs
little, and there is no finer signal on the dataset.

A transport failure or a maintenance page degrades to the epoch through
`get_json_with_retry` / `emit_freshness`, so Ghent sits out that run.  A
record without the field raises: the portal changed shape, and degrading
would freeze the city silently.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

METADATA_URL = "https://data.stad.gent/api/explore/v2.1/catalog/datasets/locaties-bomen-gent"


def fetch_modified_at() -> datetime:
    meta = get_json_with_retry(METADATA_URL)
    stamp = ((meta.get("metas") or {}).get("default") or {}).get("modified")
    if not stamp:
        raise RuntimeError("data.stad.gent locaties-bomen-gent: metas.default.modified is missing")
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)


if __name__ == "__main__":
    emit_freshness("BEGNT", fetch_modified_at)

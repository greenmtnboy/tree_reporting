#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Madrid's BIC/BIP heritage register.

The map service publishes no editingInfo and the layer has no edit-date
column, so the watermark is the catalogue's own: the `modified` stamp of the
datos.madrid.es package `300717-0-cultural-bien-mapas` that describes the
service, which the Ayuntamiento moves when it republishes the catalogue
(2026-10-02 at writing).  An outage degrades through `emit_freshness`; a
package with no parseable stamp raises.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

PACKAGE_URL = "https://datos.madrid.es/api/3/action/package_show"
PACKAGE_ID = "300717-0-cultural-bien-mapas"


def fetch_modified_at() -> datetime:
    payload = get_json_with_retry(PACKAGE_URL, params={"id": PACKAGE_ID}, timeout=120)
    result = payload.get("result") or {}
    value = result.get("modified") or result.get("metadata_modified")
    if not value:
        raise RuntimeError(f"Madrid BIC/BIP: package {PACKAGE_ID} carries no modified stamp")
    stamp = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


if __name__ == "__main__":
    emit_freshness("ESMAD", fetch_modified_at, label="ESMAD landmarks")

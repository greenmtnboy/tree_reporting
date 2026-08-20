#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

from datetime import datetime, timezone

from _ecoregion_shared import LAYER_METADATA_URL
from _ingest_shared import emit_freshness, get_json_with_retry


def fetch_data_updated_through() -> datetime:
    payload = get_json_with_retry(LAYER_METADATA_URL)

    editing_info = payload.get("editingInfo", {})
    ts_ms = editing_info.get("dataLastEditDate") or editing_info.get("lastEditDate")
    if ts_ms is None:
        raise RuntimeError("ArcGIS metadata missing editingInfo.dataLastEditDate")
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


if __name__ == "__main__":
    # No city column: the ecoregion layer is global, not per-city.
    emit_freshness(None, fetch_data_updated_through, label="ecoregion")

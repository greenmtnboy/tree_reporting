#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Atlanta's landmark and historic building sites.

The city's ArcGIS Server publishes no `editingInfo` for the layer, but each row
carries the geodatabase's own `LAST_EDITED_DATE`, so the watermark is the
latest of those.  It is read as the one newest row, ordered descending, rather
than with `field_max`: the server advertises `supportsStatistics` but answers
every `outStatistics` query, on the FeatureServer and the MapServer alike, with
"400: Unable to complete operation".  It read 2026-06-24 when the city was
wired, two weeks after the newest designation (New Hope AME Church,
2026-06-10), so the weekly landmark lane only rebuilds Atlanta's parquet when
the Department of City Planning has actually edited the register.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_datetime
from shared.ingest import emit_freshness, get_json_with_retry

LAYER = FeatureLayer(
    "https://gis.atlantaga.gov/dpcd/rest/services/OpenDataService1/FeatureServer/16"
)


def fetch_modified_at() -> datetime:
    payload = get_json_with_retry(
        LAYER.query_url,
        params={
            "where": "LAST_EDITED_DATE IS NOT NULL",
            "outFields": "LAST_EDITED_DATE",
            "orderByFields": "LAST_EDITED_DATE DESC",
            "resultRecordCount": "1",
            "returnGeometry": "false",
            "f": "json",
        },
        timeout=LAYER.timeout,
    )
    features = payload.get("features") or []
    stamp = esri_ms_to_datetime(
        (features[0].get("attributes") or {}).get("LAST_EDITED_DATE") if features else None
    )
    if stamp is None:
        raise RuntimeError(f"no LAST_EDITED_DATE came back from {LAYER.base}")
    return stamp


if __name__ == "__main__":
    emit_freshness("USATL", fetch_modified_at, label="USATL landmarks")

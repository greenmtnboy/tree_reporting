#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Prague's listed monuments.

`MAX(aktual)` over the register's polygons inside Prague's territory box --
the record's own update stamp, read as one statistics row with the same
spatial filter the ingest uses, so a designation changing in Brno does not
rebuild Prague.  The NPÚ MapServer publishes no `editingInfo`.

The statistics request is built here rather than through `field_max`
because that helper takes a `where` clause and no geometry; the Prague
filter is spatial.  A missing or empty answer raises, since that is the
layer changing shape; a transport failure raises `UpstreamUnavailable`,
which `emit_freshness` turns into the epoch so Prague sits the tick out.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import CITY_TERRITORY, emit_freshness, get_json_with_retry
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_datetime

# Kept in step with `prague_landmarks.py`.
LAYER = FeatureLayer(
    "https://geoportal.npu.cz/arcgis/rest/services/Tematicke/CP_UAP_PVO/MapServer/1"
)


def fetch_modified_at() -> datetime:
    ((lat_min, lat_max, lon_min, lon_max),) = CITY_TERRITORY["CZPRG"]
    payload = get_json_with_retry(
        LAYER.query_url,
        params={
            "where": "1=1",
            "geometry": f"{lon_min},{lat_min},{lon_max},{lat_max}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outStatistics": json.dumps(
                [{"statisticType": "max", "onStatisticField": "aktual",
                  "outStatisticFieldName": "max_value"}]
            ),
            "f": "json",
        },
        timeout=LAYER.timeout,
    )
    features = payload.get("features") or []
    attrs = (features[0].get("attributes") or {}) if features else {}
    raw = next((v for k, v in attrs.items() if k.lower() == "max_value"), None)
    stamp = esri_ms_to_datetime(raw)
    if stamp is None:
        raise RuntimeError(f"MAX(aktual) came back empty for Prague on {LAYER.base}")
    return stamp


if __name__ == "__main__":
    emit_freshness("CZPRG", fetch_modified_at, label="CZPRG landmarks")

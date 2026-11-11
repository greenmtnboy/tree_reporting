#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Naples, FL's landmarks.

The later of the two registers' own edit dates, so the weekly landmark lane
only rebuilds Naples's parquet when one of them has changed:

* the city's historic building footprints: the newest `last_edited_date`
  (2018-09-10 when the city was wired), read as one row ordered by it, since
  this server answers `outStatistics` with an error (see
  naples_update_time.py);
* the NPS National Register rows inside `CITY_BOUNDS["USNAP"]`: the newest
  `EDIT_DATE` over the handful of them (2012-08-17).

The address points and parcels that name the city's buildings are not
watched: an address change alone does not make a landmark stale.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_datetime, iter_features
from shared.ingest import CITY_BOUNDS, emit_freshness, get_json_with_retry

HISTORIC_BUILDINGS = FeatureLayer(
    "https://g.naplesgov.com/arcgis/rest/services/Building/HistoricBuildings/MapServer/0"
)
NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))


def city_last_edit() -> datetime:
    payload = get_json_with_retry(
        HISTORIC_BUILDINGS.query_url,
        params={
            "where": "last_edited_date IS NOT NULL",
            "outFields": "last_edited_date",
            "orderByFields": "last_edited_date DESC",
            "resultRecordCount": "1",
            "returnGeometry": "false",
            "f": "json",
        },
        timeout=HISTORIC_BUILDINGS.timeout,
    )
    features = payload.get("features") or []
    stamp = esri_ms_to_datetime(
        (features[0].get("attributes") or {}).get("last_edited_date") if features else None
    )
    if stamp is None:
        raise RuntimeError(f"no last_edited_date on {HISTORIC_BUILDINGS.base}")
    return stamp


def national_register_last_edit() -> datetime:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USNAP"]
    box = {
        "geometry": f"{lon_min},{lat_min},{lon_max},{lat_max}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }
    stamps = [
        esri_ms_to_datetime((feature.get("attributes") or {}).get("EDIT_DATE"))
        for layer in NRHP_LAYERS
        for page in iter_features(layer, out_fields="OBJECTID,EDIT_DATE", extra_params=box)
        for feature in page
    ]
    stamps = [s for s in stamps if s is not None]
    if not stamps:
        raise RuntimeError("no National Register EDIT_DATE inside Naples's bounds")
    return max(stamps)


def fetch_modified_at() -> datetime:
    return max(city_last_edit(), national_register_last_edit())


if __name__ == "__main__":
    emit_freshness("USNAP", fetch_modified_at, label="USNAP landmarks")

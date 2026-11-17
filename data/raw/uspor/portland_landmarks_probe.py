#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Portland, OR's landmarks.

The latest of the three registers' stamps, so the weekly landmark lane only
rebuilds Portland's parquet when one of them has changed:

* the city's Historic Landmarks and Conservation Landmarks layers: MapServer
  layers with no `editingInfo` and no edit-date column, so the City of
  Portland Hub catalogue's `modified` for each (2026-09-16 and 2024-09-12 when
  the city was wired; the Historic Landmarks entry moves when the Bureau of
  Planning and Sustainability republishes the zoning-code layers);
* the NPS National Register rows inside `CITY_BOUNDS["USPOR"]`: the newest
  `EDIT_DATE` over them (the whole box, not only the Portland rows, because a
  listing whose `City` is corrected into Portland must move it too).

`hub_last_modified` raises when the Hub no longer lists a layer -- our wiring
is wrong, not the portal down -- and degrades on an outage.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_ms_to_datetime,
    hub_last_modified,
    iter_features,
)
from shared.ingest import CITY_BOUNDS, emit_freshness

HUB = "gis-pdx.opendata.arcgis.com"
PDX = "https://www.portlandmaps.com/od/rest/services/COP_OpenData_ZoningCode/MapServer"
CITY_LAYERS = (f"{PDX}/131", f"{PDX}/133")

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))


def national_register_last_edit() -> datetime:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USPOR"]
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
        raise RuntimeError("no National Register EDIT_DATE inside Portland's bounds")
    return max(stamps)


def fetch_modified_at() -> datetime:
    return max(
        *(hub_last_modified(HUB, url) for url in CITY_LAYERS),
        national_register_last_edit(),
    )


if __name__ == "__main__":
    emit_freshness("USPOR", fetch_modified_at, label="USPOR landmarks")

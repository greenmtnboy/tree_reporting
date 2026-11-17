#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Pittsburgh, PA's landmarks.

The later of the two registers' stamps, so the weekly landmark lane only
rebuilds Pittsburgh's parquet when one of them has changed:

* the City Designated Individual Historic Sites layer: a hosted feature layer
  with `editingInfo`, so its `dataLastEditDate` (2026-09-28 when the city was
  wired; the layer is overwritten from the city's GIS, so this moves on every
  republish);
* the NPS National Register rows inside `CITY_BOUNDS["USPIT"]`: the newest
  `EDIT_DATE` over them (the probe watches the whole box, not only the
  Pittsburgh rows, because a listing whose `City` is corrected into
  Pittsburgh must move it too).
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_ms_to_datetime,
    iter_features,
    layer_last_edit,
)
from shared.ingest import CITY_BOUNDS, emit_freshness

REGISTER = FeatureLayer(
    "https://services1.arcgis.com/YZCmUqbcsUpOKfj7/arcgis/rest/services/"
    "PGHWEBCHDIndividialProperties/FeatureServer/0"
)

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))


def national_register_last_edit() -> datetime:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USPIT"]
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
        raise RuntimeError("no National Register EDIT_DATE inside Pittsburgh's bounds")
    return max(stamps)


def fetch_modified_at() -> datetime:
    return max(layer_last_edit(REGISTER), national_register_last_edit())


if __name__ == "__main__":
    emit_freshness("USPIT", fetch_modified_at, label="USPIT landmarks")

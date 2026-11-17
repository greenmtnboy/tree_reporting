#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for San José, CA's landmarks.

The later of the two registers' stamps, so the weekly landmark lane only
rebuilds San José's parquet when one of them has changed:

* the Historic Resources Inventory layer: a MapServer layer with no
  `editingInfo`, so the newest `LASTUPDATE` over the whole layer (2024-01-19
  when the city was wired) -- a designation is an edit to an inventory row --
  and, as a backstop for an edit the column does not stamp, its ArcGIS
  item's `modified`;
* the NPS National Register rows inside `CITY_BOUNDS["USSJC"]`: the newest
  `EDIT_DATE` over them (the whole box, not only the San José rows, because a
  listing whose `City` is corrected into San José must move it too).
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_ms_to_datetime,
    field_max,
    iter_features,
)
from shared.ingest import CITY_BOUNDS, emit_freshness, get_json_with_retry

INVENTORY = FeatureLayer(
    "https://geo.sanjoseca.gov/server/rest/services/OPN/OPN_OpenDataService/MapServer/406"
)
INVENTORY_ITEM = "c4eb3973a70d40b688fbe6971035761b"
ITEM_URL = f"https://www.arcgis.com/sharing/rest/content/items/{INVENTORY_ITEM}"

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))


def inventory_item_modified() -> datetime:
    payload = get_json_with_retry(ITEM_URL, params={"f": "json"}, timeout=60)
    stamp = esri_ms_to_datetime(payload.get("modified"))
    if stamp is None:
        raise RuntimeError(f"no `modified` on ArcGIS item {INVENTORY_ITEM}")
    return stamp


def national_register_last_edit() -> datetime:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USSJC"]
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
        raise RuntimeError("no National Register EDIT_DATE inside San José's bounds")
    return max(stamps)


def fetch_modified_at() -> datetime:
    return max(
        field_max(INVENTORY, "LASTUPDATE"),
        inventory_item_modified(),
        national_register_last_edit(),
    )


if __name__ == "__main__":
    emit_freshness("USSJC", fetch_modified_at, label="USSJC landmarks")

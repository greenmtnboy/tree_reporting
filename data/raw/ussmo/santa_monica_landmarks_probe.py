#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Santa Monica, CA's landmarks.

The later of the two registers' stamps, so the weekly landmark lane only
rebuilds Santa Monica's parquet when one of them has changed:

* the city's Historic Landmarks and Structures of Merit layer: the newest
  designation date over its rows (2024-06-10 when the city was wired),
  which moves when the Landmarks Commission designates a place.  The layer
  has no `editingInfo` and stores `desig_date` as text (`m/d/yyyy`), so
  `field_max` cannot read it; the 158 rows are read and parsed here, with
  the ingest's own parser, so a date the ingest cannot read fails both.  A
  removal or a corrected address moves nothing until the next designation.
* the NPS National Register rows inside `CITY_BOUNDS["USSMO"]`: the newest
  `EDIT_DATE` over them (the whole box, not only the Santa Monica rows, so a
  listing whose `City` is corrected into Santa Monica moves it too).
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_datetime, iter_features
from shared.ingest import CITY_BOUNDS, emit_freshness

sys.path.insert(0, str(Path(__file__).parent))
from santa_monica_landmarks import NRHP_LAYERS, REGISTER, clean  # noqa: E402


def designated_at(value) -> datetime | None:
    text = clean(value)
    if text is None:
        return None
    for fmt in ("%m/%d/%Y", "%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise RuntimeError(f"Santa Monica landmark desig_date {text!r} is not m/d/yyyy")


def register_last_designation() -> datetime:
    stamps = [
        designated_at((feature.get("attributes") or {}).get("desig_date"))
        for page in iter_features(REGISTER, out_fields="objectid,desig_date", order_by="objectid")
        for feature in page
    ]
    stamps = [s for s in stamps if s is not None]
    if not stamps:
        raise RuntimeError(f"no desig_date on {REGISTER.base}")
    return max(stamps)


def national_register_last_edit() -> datetime:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USSMO"]
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
        raise RuntimeError("no National Register EDIT_DATE inside Santa Monica's bounds")
    return max(stamps)


def fetch_modified_at() -> datetime:
    return max(register_last_designation(), national_register_last_edit())


if __name__ == "__main__":
    emit_freshness("USSMO", fetch_modified_at, label="USSMO landmarks")

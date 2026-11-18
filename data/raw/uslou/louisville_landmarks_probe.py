#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Louisville's landmarks.

The latest of three stamps, so the weekly landmark lane only rebuilds
Louisville's parquet when a register has changed:

* the Metro landmarks layer (LOJIC `OpenDataDevelopment/MapServer/4`): a
  MapServer layer with no `editingInfo`, but every row carries `REV_DATE`, a
  `YYYY.MM.DD` string (2025.11.03 when the city was wired), read as the
  maximum over the layer -- `field_max` wants an Esri date field, so it is
  parsed here;
* the same dataset's `modified` in the Louisville Metro Open Data catalogue
  (data.louisvilleky.gov's DCAT feed), the backstop for an edit that did not
  touch `REV_DATE`;
* the NPS National Register rows inside `CITY_BOUNDS["USLOU"]`: the newest
  `EDIT_DATE` over them (the whole box, so a listing whose county is corrected
  into Jefferson moves it too).
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_ms_to_datetime,
    hub_last_modified,
    iter_features,
)
from shared.ingest import CITY_BOUNDS, emit_freshness, get_json_with_retry

METRO = FeatureLayer(
    "https://gis.lojic.org/maps/rest/services/LojicSolutions/OpenDataDevelopment/MapServer/4"
)
HUB_HOST = "data.louisvilleky.gov"

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))

REV_DATE = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})$")


def metro_rev_date() -> datetime:
    payload = get_json_with_retry(
        METRO.query_url,
        params={
            "where": "1=1",
            "outStatistics": json.dumps(
                [{"statisticType": "max", "onStatisticField": "REV_DATE",
                  "outStatisticFieldName": "m"}]
            ),
            "f": "json",
        },
        timeout=METRO.timeout,
    )
    try:
        value = next(iter(payload["features"][0]["attributes"].values()))
    except (KeyError, IndexError, StopIteration, TypeError) as exc:
        raise RuntimeError(f"no REV_DATE statistic from {METRO.base}: {payload!r}"[:500]) from exc
    match = REV_DATE.match(str(value or "").strip())
    if not match:
        raise RuntimeError(f"Louisville landmarks REV_DATE {value!r} is not YYYY.MM.DD")
    y, m, d = (int(g) for g in match.groups())
    return datetime(y, m, d, tzinfo=timezone.utc)


def national_register_last_edit() -> datetime:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USLOU"]
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
        raise RuntimeError("no National Register EDIT_DATE inside Louisville's bounds")
    return max(stamps)


def fetch_modified_at() -> datetime:
    return max(
        metro_rev_date(),
        hub_last_modified(HUB_HOST, METRO.base),
        national_register_last_edit(),
    )


if __name__ == "__main__":
    emit_freshness("USLOU", fetch_modified_at, label="USLOU landmarks")

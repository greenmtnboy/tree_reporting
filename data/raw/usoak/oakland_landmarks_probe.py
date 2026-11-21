#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Oakland, CA's landmarks.

The later of the two registers' stamps, so the weekly landmark lane only
rebuilds Oakland's parquet when one of them has changed:

* the City Landmarks layer (`Planning_and_Zoning_Controls/MapServer/35`), a
  MapServer layer with no `editingInfo` and no date column.  Each row's
  `LASTUPDATE` is the month the Bureau of Planning last touched it, as text
  ("June 2005", "Jan 2020", "JAN 2024": JAN 2024 when the city was wired);
  the probe is the newest of them, read as the first of that month.  A row
  whose value cannot be read is skipped, but a layer with none readable, or
  without the column, raises: that is a schema change.
* the NPS National Register rows inside `CITY_BOUNDS["USOAK"]`: the newest
  `EDIT_DATE` over them (the whole box, not only the Oakland rows, so a
  listing whose `City` is corrected into Oakland moves it too).
"""

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_datetime, iter_features
from shared.ingest import CITY_BOUNDS, emit_freshness

REGISTER = FeatureLayer(
    "https://gismaps.oaklandca.gov/server/rest/services/Planning_and_Zoning_Controls/MapServer/35"
)

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))

MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
)}
MONTH_YEAR = re.compile(r"^([A-Za-z]{3})[A-Za-z]*\.?\s+(\d{4})$")


def parse_month_year(value) -> datetime | None:
    match = MONTH_YEAR.match(" ".join(str(value or "").split()))
    if not match or match.group(1).lower() not in MONTHS:
        return None
    return datetime(int(match.group(2)), MONTHS[match.group(1).lower()], 1, tzinfo=timezone.utc)


def register_last_update() -> datetime:
    rows = [
        feature.get("attributes") or {}
        for page in iter_features(REGISTER, out_fields="OBJECTID,LASTUPDATE")
        for feature in page
    ]
    if not rows or any("LASTUPDATE" not in row for row in rows):
        raise RuntimeError(f"{REGISTER.base} has no LASTUPDATE column")
    stamps = [s for s in (parse_month_year(row["LASTUPDATE"]) for row in rows) if s is not None]
    if not stamps:
        raise RuntimeError(f"no readable LASTUPDATE in {REGISTER.base}")
    return max(stamps)


def national_register_last_edit() -> datetime:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USOAK"]
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
        raise RuntimeError("no National Register EDIT_DATE inside Oakland's bounds")
    return max(stamps)


def fetch_modified_at() -> datetime:
    return max(register_last_update(), national_register_last_edit())


if __name__ == "__main__":
    emit_freshness("USOAK", fetch_modified_at, label="USOAK landmarks")

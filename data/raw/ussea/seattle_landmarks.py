#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Seattle, WA's designated City Landmarks and National Register listings.

Two registers, both read live from ArcGIS layers:

* **Seattle City Landmarks**, the city's own designation register: "sites,
  buildings, vehicles, vessels, and street clocks that have been designated
  as Historic City Landmarks subject to protection by City Ordinance"
  (Seattle Municipal Code 25.12, administered by the Landmarks Preservation
  Board in the Department of Neighborhoods).  Published by SDCI as the
  `Landmarks/FeatureServer/0` layer on the city's ArcGIS Online (item
  462a18bf1f9f459eb7fd8ea08c843880, refreshed daily), listed on
  data.seattle.gov under the city's Open Data Terms of Use (no named licence;
  "data on this site does not require specific attribution").  517 points on
  2026-11-16 (721 rows with the 204 National Register listings), each with a name, address and the landmark number `LANDNO`
  (unique over the layer, checked here), which keys the row (`sea-lm-125`).
  `EFF_DATE`, the date the designating ordinance took effect, gives
  `year_designated` (304 rows); it is null on 155 rows and earlier than the
  city's 1973 ordinance on the rest -- 1899-12-30, the spreadsheet zero -- so
  any year before 1970 is published as null.  Moveable landmarks (the
  vessels at Lake Union Park, several street clocks) sit at their current
  mooring or address, as the city maps them.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) within
  `CITY_BOUNDS["USSEA"]` and with `City = Seattle`; the box also holds a few
  Shoreline, Burien and Mercer Island listings.  Listed buildings, sites and
  structures, keyed `sea-nr-{NRIS_Refnum}` with `CertDate` as
  `year_designated`; districts are areas and are not published (Columbus's
  and Durham's rule).  NPS data is a US federal work.

Many buildings are on both registers; both rows are kept, as Columbus and
Naperville keep a building on two registers, because the two designations
are separate facts.  `lm_type` says which register each row is from.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import CITY_BOUNDS, emit, make_point_wkt

REGISTER = FeatureLayer(
    "https://services.arcgis.com/ZOyb2t4B0UYuYNYH/arcgis/rest/services/Landmarks/FeatureServer/0"
)
REGISTER_FIELDS = "OBJECTID,NAME,ADDRESS,LANDNO,EFF_DATE"

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,City,CertDate,STATUS"

LOCAL_TYPE = "Seattle City Landmark"
NATIONAL_TYPE = "National Register of Historic Places"

# The Landmarks Preservation Ordinance is from 1973; an earlier `EFF_DATE`
# is a placeholder (1899-12-30, the spreadsheet zero), not a designation.
EARLIEST_DESIGNATION_YEAR = 1970

# The layer's address placeholder for a landmark with no street address.
NO_ADDRESS = frozenset({"XXXXX", ""})

COLUMNS = (
    "landmark_id",
    "name",
    "geometry_raw",
    "address",
    "lm_type",
    "register_number",
    "year_designated",
)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def esri_year(value) -> int | None:
    """The year of an Esri epoch-ms value; negative values included (Windows'
    `fromtimestamp` refuses those)."""
    if value is None:
        return None
    try:
        return (_EPOCH + timedelta(milliseconds=float(value))).year
    except (OverflowError, ValueError, TypeError):
        return None


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def landmark_number(value) -> str | None:
    if value is None:
        return None
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


def seattle_register() -> dict[str, list]:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    features = fetch_all(REGISTER, out_fields=REGISTER_FIELDS, return_geometry=True, out_sr=4326)
    if not features:
        raise RuntimeError(f"{REGISTER.base} returned no landmarks")
    seen: set[str] = set()
    for feature in sorted(features, key=lambda f: f["attributes"]["OBJECTID"]):
        attrs = feature.get("attributes") or {}
        geom = feature.get("geometry") or {}
        name = clean(attrs.get("NAME"))
        number = landmark_number(attrs.get("LANDNO"))
        wkt = make_point_wkt(geom.get("x"), geom.get("y"))
        if not name or not number or wkt is None:
            continue
        if number in seen:
            raise RuntimeError(f"Seattle landmark number {number} is repeated")
        seen.add(number)
        year = esri_year(attrs.get("EFF_DATE"))
        address = clean(attrs.get("ADDRESS"))
        rows["landmark_id"].append(f"sea-lm-{number}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(None if address in NO_ADDRESS else address)
        rows["lm_type"].append(LOCAL_TYPE)
        rows["register_number"].append(number)
        rows["year_designated"].append(
            year if year is not None and year >= EARLIEST_DESIGNATION_YEAR else None
        )
    return rows


def national_register() -> dict[str, list]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USSEA"]
    box = {
        "geometry": f"{lon_min},{lat_min},{lon_max},{lat_max}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }
    seen: set[str] = set()
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    for layer in NRHP_LAYERS:
        for feature in fetch_all(
            layer, out_fields=NRHP_FIELDS, return_geometry=True, out_sr=4326, extra_params=box
        ):
            attrs = feature.get("attributes") or {}
            ref = clean(attrs.get("NRIS_Refnum"))
            name = clean(attrs.get("RESNAME"))
            wkt = esri_geometry_to_wkt(feature.get("geometry"))
            if not ref or not name or wkt is None or ref in seen:
                continue
            if (clean(attrs.get("City")) or "").lower() != "seattle":
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                continue
            seen.add(ref)
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"sea-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["register_number"].append(ref)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    return rows


def build() -> pa.Table:
    local = seattle_register()
    national = national_register()
    data = {k: local[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USSEA"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

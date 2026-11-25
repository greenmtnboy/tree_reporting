#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Oxnard, CA's historic landmarks and National Register listings.

Two registers, both read live from ArcGIS layers:

* **The designated historic landmarks and points of interest in Oxnard**,
  read from the City's "Ventura County Designated Historic Landmarks and
  Points of Interest" layer (`HistoricData/MapServer/0` on maps.oxnard.org,
  the same server as the trees; "compiled by the City of Oxnard GIS
  Division").  26 points on 2026-11-23: 24 `Landmark` and 2 `Point of
  Interest`, numbered in Ventura County's landmark list (`ListNo`, the
  `register_number`), several designated by Oxnard City Council resolution
  (`CCResolution`).  Keyed `oxnard-lm-{ListNo}` (non-null and unique over the
  layer; a repeat raises).  The Henry T. Oxnard District is an area and is
  not published (Durham's rule).  The layer records no designation date, so
  `year_designated` is null.  Licence: none stated; displayed with
  attribution only, not licensed for reuse (the service credits "The City of
  Oxnard GIS Division" and publishes no licence; the City's GIS items carry a
  no-warranty disclaimer only).
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) within
  `CITY_BOUNDS["USOXR"]` and with `City = Oxnard` -- the box also holds
  listings filed under Port Hueneme and Ventura.  Listed buildings, sites and
  structures, keyed `oxnard-nr-{NRIS_Refnum}` with `CertDate` as
  `year_designated`; districts are areas and are not published.  NPS data is
  a US federal work.

The Carnegie library is on both (the County list's Oxnard Carnegie Library,
the National Register's Oxnard Public Library); both rows are kept, as
Columbus keeps them, because the two records are separate facts.  `lm_type`
says which register each row is from.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import emit
from shared.nrhp import national_register as shared_national_register  # noqa: E402

LANDMARKS = FeatureLayer(
    "https://maps.oxnard.org/arcgis/rest/services/HistoricData/MapServer/0"
)
LANDMARK_FIELDS = "OBJECTID,ListNo,DesType,Name,Address"
# `DesType` -> lm_type.  An unseen designation type raises.
DES_TYPES = {
    "landmark": "Ventura County historic landmark",
    "point of interest": "Ventura County point of interest",
}
# Areas, not points: not published.
AREA_NAMES = frozenset({"henry t. oxnard district"})


COLUMNS = (
    "landmark_id",
    "name",
    "geometry_raw",
    "address",
    "lm_type",
    "register_number",
    "year_designated",
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("“", '"').replace("”", '"').split())
    return text or None


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def designated_landmarks() -> dict[str, list]:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    features = fetch_all(
        LANDMARKS, out_fields=LANDMARK_FIELDS, return_geometry=True, out_sr=4326
    )
    if not features:
        raise RuntimeError(f"{LANDMARKS.base} returned no landmarks")
    seen: set[str] = set()
    for feature in features:
        attrs = feature.get("attributes") or {}
        number = attrs.get("ListNo")
        if number is None:
            raise RuntimeError(f"Oxnard landmark OBJECTID {attrs.get('OBJECTID')} has no ListNo")
        ref = str(int(number)) if float(number).is_integer() else str(number)
        if ref in seen:
            raise RuntimeError(f"Oxnard landmark ListNo {ref} is repeated")
        seen.add(ref)
        des_type = (clean(attrs.get("DesType")) or "").lower()
        if des_type not in DES_TYPES:
            raise RuntimeError(f"Oxnard landmarks: unseen DesType {attrs.get('DesType')!r}")
        name = clean(attrs.get("Name"))
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if not name or wkt is None or name.lower() in AREA_NAMES:
            continue
        rows["landmark_id"].append(f"oxnard-lm-{ref}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(clean(attrs.get("Address")))
        rows["lm_type"].append(DES_TYPES[des_type])
        rows["register_number"].append(ref)
        rows["year_designated"].append(None)
    return rows


def national_register() -> dict[str, list]:
    return shared_national_register("USOXR", "Oxnard", "oxnard", COLUMNS, required=False)


def build() -> pa.Table:
    local = designated_landmarks()
    national = national_register()
    data = {k: local[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USOXR"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

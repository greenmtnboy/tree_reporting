#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Baton Rouge, LA's local historic landmarks and National Register listings.

Two registers, both read live from ArcGIS layers:

* **The City-Parish's local landmarks**, the "Local Landmark" layer of the
  EBRGIS `Cadastral/Historic` MapServer on maps.brla.gov (ArcGIS item
  ef927acb261c48c3bbef735602b9f5dc, "National and local historic districts
  and landmarks in East Baton Rouge Parish").  The item's licence field reads
  "For public use" followed by the City-Parish's reference-only disclaimer;
  no named licence.  7 parcel polygons on 2026-11-22, names in capitals
  (title-cased here).  The layer has no register number; the key is its
  OBJECTID `ID` (`btr-ll-{ID}`), and a repeated one raises.  `DATE_BUILT` is
  the construction year, not a designation year, and is not read.  The
  "Local District" layer is areas and is not read; the "National Landmark"
  layer is the City-Parish's copy of the National Register parcels and is
  superseded by the NPS's own listing below.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) within
  `CITY_BOUNDS["USBTR"]` and with `County = East Baton Rouge` -- the box also
  holds Port Allen and Brusly (West Baton Rouge), Plaquemine and St. Gabriel
  (Iberville) and Denham Springs (Livingston).  Listed buildings, sites,
  structures and objects, keyed `btr-nr-{NRIS_Refnum}` with `CertDate` as
  `year_designated`; districts are areas and are not published (Durham's
  rule).  NPS data is a US federal work.

A property on both registers keeps both rows, as Columbus does: the two
designations are separate facts.  `lm_type` says which register each row is
from.
"""

import string
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import CITY_BOUNDS, emit

LOCAL = FeatureLayer("https://maps.brla.gov/gis/rest/services/Cadastral/Historic/MapServer/1")
LOCAL_FIELDS = "ID,NAME,TYPE,JURISDICTION"

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,City,County,CertDate,STATUS"
COUNTY = "east baton rouge"

LOCAL_TYPE = "Baton Rouge local historic landmark"
NATIONAL_TYPE = "National Register of Historic Places"

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
    text = " ".join(str(value).split())
    return text or None


def title(text: str) -> str:
    """`ST. JOSEPH'S ACADEMY` -> `St. Joseph's Academy`."""
    return string.capwords(text.lower()) if text == text.upper() else text


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def local_register() -> dict[str, list]:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    features = fetch_all(
        LOCAL, out_fields=LOCAL_FIELDS, return_geometry=True, out_sr=4326, order_by="ID"
    )
    if not features:
        raise RuntimeError(f"{LOCAL.base} returned no landmarks")
    seen: set[int] = set()
    for feature in features:
        attrs = feature.get("attributes") or {}
        oid = attrs.get("ID")
        if oid in seen:
            raise RuntimeError(f"Baton Rouge Local Landmark ID {oid} is repeated")
        seen.add(oid)
        name = clean(attrs.get("NAME"))
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if not name or wkt is None:
            continue
        rows["landmark_id"].append(f"btr-ll-{oid}")
        rows["name"].append(title(name))
        rows["geometry_raw"].append(wkt)
        rows["address"].append(None)
        rows["lm_type"].append(LOCAL_TYPE)
        rows["register_number"].append(None)
        rows["year_designated"].append(None)
    return rows


def national_register() -> dict[str, list]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USBTR"]
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
            if (clean(attrs.get("County")) or "").lower() != COUNTY:
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                continue
            seen.add(ref)
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"btr-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["register_number"].append(ref)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    return rows


def build() -> pa.Table:
    local = local_register()
    national = national_register()
    data = {k: local[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USBTR"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

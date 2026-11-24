#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Davenport, Iowa's local historic landmarks and National Register listings.

Two registers, both read live from ArcGIS layers:

* **The City's Local Historic Landmarks**, the hosted layer
  `Hosted/Individually_Listed_LHL/FeatureServer/3` on the City's own server
  gis.davenportiowa.com (portal item ee0cc2a528b94b9e9508371957690d58,
  "Individually Listed LHL Updated 2025", credited "P&Z": "current standing
  and destroyed local historical landmarks for HPC and interested parties
  ... updated frequently by Planning & Zoning").  No licence is stated (the
  item's `licenseInfo` is empty); displayed with attribution only.  62
  points on 2026-11-23; the four with `present = 0` are destroyed and are
  not published.  Keyed by the layer's GlobalID (`davenport-lhl-{guid}`):
  the layer has no register number, and two landmarks share a name.
  `datenomination` is the nomination date in a mix of formats, not the
  designation, and is not read.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons)
  within `CITY_BOUNDS["USDVN"]` and with `City = Davenport`, `State = IOWA`
  -- the box also holds Rock Island and Moline (Illinois) across the river
  and the west of Bettendorf.  Listed buildings, sites, structures and
  objects, keyed `davenport-nr-{NRIS_Refnum}` with `CertDate` as
  `year_designated`; districts are areas and are not published (Durham's
  rule).  NPS data is a US federal work.

A property on both registers keeps both rows, as Columbus and Baton Rouge
do: the two designations are separate facts.  `lm_type` says which register
each row is from.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features  # noqa: E402
from shared.ingest import CITY_BOUNDS, emit  # noqa: E402

LOCAL = FeatureLayer(
    "https://gis.davenportiowa.com/arcgis/rest/services/Hosted/"
    "Individually_Listed_LHL/FeatureServer/3"
)
LOCAL_FIELDS = "objectid,globalid,name,address,present"

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,City,State,CertDate,STATUS"
CITY = "davenport"
STATE = "iowa"

LOCAL_TYPE = "Davenport local historic landmark"
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


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def local_register() -> dict[str, list]:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    features = fetch_all(
        LOCAL, out_fields=LOCAL_FIELDS, return_geometry=True, out_sr=4326, order_by="objectid"
    )
    if not features:
        raise RuntimeError(f"{LOCAL.base} returned no landmarks")
    seen: set[str] = set()
    for feature in features:
        attrs = feature.get("attributes") or {}
        guid = str(attrs.get("globalid") or "").strip("{} ").lower()
        if not guid:
            continue
        if guid in seen:
            raise RuntimeError(f"Davenport landmark GlobalID {guid} is repeated")
        seen.add(guid)
        if attrs.get("present") == 0:  # destroyed
            continue
        name = clean(attrs.get("name"))
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if not name or wkt is None:
            continue
        rows["landmark_id"].append(f"davenport-lhl-{guid}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(clean(attrs.get("address")))
        rows["lm_type"].append(LOCAL_TYPE)
        rows["register_number"].append(None)
        rows["year_designated"].append(None)
    return rows


def national_register() -> dict[str, list]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USDVN"]
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
            if (clean(attrs.get("City")) or "").lower() != CITY:
                continue
            if (clean(attrs.get("State")) or "").lower() != STATE:
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                continue
            seen.add(ref)
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"davenport-nr-{ref}")
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
            "city": pa.array(["USDVN"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

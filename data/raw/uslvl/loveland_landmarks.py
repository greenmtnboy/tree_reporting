#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Loveland, Colorado's designated local historic landmarks and National Register listings.

Two registers, both read live from ArcGIS layers:

* **The City's designated local historic properties**: the hosted layer
  `Historic_Properties/FeatureServer/5` "Historic Survey" in the City's own
  ArcGIS organisation (item e30cf1adb226409593f4abc8f58a251d, "Historic
  Property Survey within Loveland's Growth Management Area", originator
  "City of Loveland, Colorado - Development Services Department"), filtered
  as the City's own "Designated Local Historic Landmarks" map does
  (item c85409ad6daa40b4be6daf0436877e11): `LOCAL = 'Designated'`, 82 of
  2,317 surveyed properties on 2026-11-27 (individual landmarks and the
  contributing buildings of the locally designated districts).  No licence
  is stated (the item's `licenseInfo` is empty); displayed with attribution
  only.  Keyed by the Colorado OAHP site number (`loveland-lhl-{SITENO}`,
  `5LR6046`); one building surveyed as two storefront points shares a
  number and publishes once, and the two rows without one are keyed by
  OBJECTID.  A row marked `DEMOLISHED` is not published.  Half the rows
  carry no `NAME`; their name is the address, which is how the City's map
  labels them.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons)
  within `CITY_BOUNDS["USLVL"]` and with `City = Loveland`, `State =
  COLORADO` (the box also holds a Berthoud farm).  Listed buildings, sites,
  structures and objects, keyed `loveland-nr-{NRIS_Refnum}` with `CertDate`
  as `year_designated`; districts are areas and are not published (Durham's
  rule).  NPS data is a US federal work.

A property on both registers keeps both rows, as Columbus and Davenport do:
the two designations are separate facts.  `lm_type` says which register
each row is from.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features  # noqa: E402
from shared.ingest import CITY_BOUNDS, emit  # noqa: E402

LOCAL = FeatureLayer(
    "https://services1.arcgis.com/d4MV7N9xmuHYbLrF/arcgis/rest/services/"
    "Historic_Properties/FeatureServer/5"
)
LOCAL_FIELDS = "OBJECTID,SITENO,NAME,ADDRESS,LOCAL,DEMOLISHED"
LOCAL_WHERE = "LOCAL = 'Designated'"

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,City,State,CertDate,STATUS"
CITY = "loveland"
STATE = "colorado"

LOCAL_TYPE = "Loveland designated local historic property"
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
        LOCAL,
        out_fields=LOCAL_FIELDS,
        where=LOCAL_WHERE,
        return_geometry=True,
        out_sr=4326,
        order_by="OBJECTID",
    )
    if not features:
        raise RuntimeError(f"{LOCAL.base} returned no designated properties")
    seen: set[str] = set()
    for feature in features:
        attrs = feature.get("attributes") or {}
        if (clean(attrs.get("DEMOLISHED")) or "").lower() in ("yes", "relocated"):
            continue
        site = clean(attrs.get("SITENO"))
        key = site or f"oid-{attrs['OBJECTID']}"
        if key in seen:
            continue
        address = clean(attrs.get("ADDRESS"))
        if address and address.lower() == "no address":
            address = None
        name = clean(attrs.get("NAME")) or address
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if not name or wkt is None:
            continue
        seen.add(key)
        rows["landmark_id"].append(f"loveland-lhl-{key}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(address)
        rows["lm_type"].append(LOCAL_TYPE)
        rows["register_number"].append(site)
        rows["year_designated"].append(None)
    return rows


def national_register() -> dict[str, list]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USLVL"]
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
            rows["landmark_id"].append(f"loveland-nr-{ref}")
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
            "city": pa.array(["USLVL"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

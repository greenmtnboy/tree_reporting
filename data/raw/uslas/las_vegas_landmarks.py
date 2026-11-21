#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Las Vegas, NV's Historic Property Register and National Register listings.

Two registers, both read live from ArcGIS layers:

* **The City of Las Vegas Historic Property Register** -- the properties the
  City Council has listed on the City's own register (Las Vegas Municipal
  Code Title 19, Historic Preservation; Historic Preservation Commission),
  published by the Department of Planning as `HistoricAreas/FeatureServer/163`
  ("Historic_Areas", ArcGIS item ca287f8cdc29447b97c2bc6ece34f67a, owner
  `OfficeOfGIS_LasVegas`).  31 parcel polygons on 2026-11-20; `TYP` codes the
  registers each is on (`N` National, `S` State, `L` Local, combined as
  `NSL`), with a date column per register.  Only the rows on the City's
  register (`L` in `TYP`, 23) are read; the national-only rows come from the
  NPS below, and the state register has no layer of its own.  Historic
  districts (`... Historic District`, `... Neighborhood District`: John S.
  Park, Berkley Square, Beverly Green, Lorenzi Park) are areas, not landmarks,
  and are not published (Durham's rule), which leaves 19 properties.  **No
  licence stated; displayed with attribution only, not licensed for reuse**
  -- the item's `licenseInfo` is a no-warranty disclaimer only.  Keyed
  `las-hp-{NUM}` (the layer's own site number, unique; OBJECTID is dense
  1..N and the layer is republished whole); `register_number` is the City
  resolution (`R-65-94`, `22-0080-HPC1`) where the layer records one, and
  `year_designated` the year of the `LOCAL` listing date.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) within
  `CITY_BOUNDS["USLAS"]`, `County` = Clark in Nevada, **and inside the City's
  limits** (`City_Limits_View/FeatureServer/0`, the City's own boundary,
  fetched once and tested locally): North Las Vegas and unincorporated Clark
  County (Paradise, Sunrise Manor) share the box and the "Las Vegas" mailing
  city.  A polygon listing is placed by the mean of its outer ring.  Listed
  buildings, sites, structures and objects, keyed `las-nr-{NRIS_Refnum}` with
  `CertDate` as `year_designated`; districts are areas and are not published.
  NPS data is a US federal work.

A building on both registers keeps both rows, as Columbus and Richmond do:
the two designations are separate facts.  `lm_type` says which register each
row is from.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_geometry_to_wkt,
    esri_ms_to_datetime,
    iter_features,
)
from shared.ingest import CITY_BOUNDS, emit

CITY_REGISTER = FeatureLayer(
    "https://services1.arcgis.com/F1v0ufATbBQScMtY/arcgis/rest/services/"
    "HistoricAreas/FeatureServer/163"
)
CITY_FIELDS = "OBJECTID,NUM,TYP,NAME,RESONO,LOCAL,ADDRESS"

CITY_LIMITS = FeatureLayer(
    "https://services1.arcgis.com/F1v0ufATbBQScMtY/arcgis/rest/services/"
    "City_Limits_View/FeatureServer/0"
)

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,County,State,CertDate,STATUS"

LOCAL_TYPE = "City of Las Vegas Historic Property Register"
NATIONAL_TYPE = "National Register of Historic Places"

# A City register row whose name says it is an area rather than a property.
DISTRICT_WORDS = ("district",)

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


def city_register() -> dict[str, list]:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    features = fetch_all(CITY_REGISTER, out_fields=CITY_FIELDS, return_geometry=True, out_sr=4326)
    if not features:
        raise RuntimeError(f"{CITY_REGISTER.base} returned no sites")
    seen: set[str] = set()
    for feature in sorted(features, key=lambda f: f["attributes"]["OBJECTID"]):
        attrs = feature.get("attributes") or {}
        if "L" not in (clean(attrs.get("TYP")) or "").upper():
            continue
        name = clean(attrs.get("NAME"))
        num = clean(attrs.get("NUM"))
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if not name or not num or wkt is None:
            continue
        if any(word in name.lower() for word in DISTRICT_WORDS):
            continue
        if num in seen:
            raise RuntimeError(f"Las Vegas register: NUM {num} repeats; it is no longer an id")
        seen.add(num)
        listed = esri_ms_to_datetime(attrs.get("LOCAL"))
        rows["landmark_id"].append(f"las-hp-{num}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(clean(attrs.get("ADDRESS")))
        rows["lm_type"].append(LOCAL_TYPE)
        rows["register_number"].append(clean(attrs.get("RESONO")))
        rows["year_designated"].append(listed.year if listed else None)
    if not rows["landmark_id"]:
        raise RuntimeError("no City register properties in the Las Vegas layer")
    return rows


def city_limit_rings() -> list[list[list[float]]]:
    """Every ring of the City limits, in WGS84.  Holes (county islands) are
    rings too: the even-odd test below treats them as outside."""
    features = fetch_all(CITY_LIMITS, out_fields="FID", return_geometry=True, out_sr=4326, order_by="FID")
    rings = [ring for f in features for ring in ((f.get("geometry") or {}).get("rings") or [])]
    if not rings:
        raise RuntimeError(f"{CITY_LIMITS.base} returned no boundary")
    return rings


def inside(rings: list[list[list[float]]], lon: float, lat: float) -> bool:
    """Even-odd point-in-polygon over every ring."""
    hit = False
    for ring in rings:
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
            if (y1 > lat) != (y2 > lat):
                x = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
                if x > lon:
                    hit = not hit
    return hit


def anchor(geometry: dict | None) -> tuple[float, float] | None:
    """A point for the limits test: the point itself, or the outer ring's mean."""
    if not geometry:
        return None
    if geometry.get("x") is not None:
        return float(geometry["x"]), float(geometry["y"])
    rings = geometry.get("rings") or []
    if not rings or not rings[0]:
        return None
    ring = rings[0]
    return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)


def national_register() -> dict[str, list]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USLAS"]
    box = {
        "geometry": f"{lon_min},{lat_min},{lon_max},{lat_max}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }
    limits = city_limit_rings()
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
            if (clean(attrs.get("County")) or "").lower() != "clark":
                continue
            if (clean(attrs.get("State")) or "").upper() != "NEVADA":
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                continue
            point = anchor(feature.get("geometry"))
            if point is None or not inside(limits, *point):
                continue
            seen.add(ref)
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"las-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["register_number"].append(ref)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    if not rows["landmark_id"]:
        raise RuntimeError("no National Register listings inside the Las Vegas city limits")
    return rows


def build() -> pa.Table:
    local = city_register()
    national = national_register()
    data = {k: local[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    print(
        f"Las Vegas landmarks: {len(local['landmark_id'])} City register, "
        f"{len(national['landmark_id'])} National Register",
        file=sys.stderr,
    )
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USLAS"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

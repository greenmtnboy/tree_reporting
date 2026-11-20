#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Richmond, VA's City Old and Historic sites and National Register listings.

Two registers, both read live from ArcGIS layers:

* **City Old and Historic sites** -- the individual properties the City has
  designated by ordinance as Old and Historic Districts of their own (City
  Code ch. 30, art. IX; subject to Commission of Architectural Review
  approval), published by Planning & Development Review as
  `City_Old_and_Historic_Sites/FeatureServer/0` (ArcGIS item
  4d8df7dc38ca4719a4d02a77991dd7ec, owner `richard.morton_cor`).  29
  parcel polygons on 2026-11-19 (Wilton, the John Marshall House, Linden Row,
  the Jefferson Hotel, ...).  **No licence stated; displayed with attribution
  only, not licensed for reuse** -- the item's `licenseInfo` is a liability
  disclaimer only.  Keyed `ric-ohs-{GlobalID}`; `register_number` is the last
  ordinance affecting the site (`1970-111-118`).  `year_designated` is left
  null: the layer records only the *last affecting* adoption date, which for
  some sites is an amendment (the White House of the Confederacy reads 2018).
  The City's multi-property Old and Historic Districts are areas, not
  landmarks, and are not read.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) within
  `CITY_BOUNDS["USRIC"]` and with `County` = Richmond in Virginia -- the
  independent city.  The box also holds Henrico and Chesterfield listings
  whose `City` reads Richmond (a mailing address), so `County` is the filter,
  not `City`.  Listed buildings, sites, structures and objects, keyed
  `ric-nr-{NRIS_Refnum}` with `CertDate` as `year_designated`; districts are
  areas and are not published (Durham's rule).  NPS data is a US federal
  work.  The City's own "State and Federal Historic Sites" layer is a 2019
  copy of the state and national registers and is not read.

A building on both registers keeps both rows, as Columbus does: the two
designations are separate facts.  `lm_type` says which register each row is
from.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_geometry_to_wkt,
    iter_features,
)
from shared.ingest import CITY_BOUNDS, emit

CITY_SITES = FeatureLayer(
    "https://services1.arcgis.com/k3vhq11XkBNeeOfM/arcgis/rest/services/"
    "City_Old_and_Historic_Sites/FeatureServer/0"
)
CITY_FIELDS = "OBJECTID,GlobalID,SiteName,Address,LastAffectingOrdinance"

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,County,State,CertDate,STATUS"

LOCAL_TYPE = "City of Richmond Old and Historic Site"
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


def city_sites() -> dict[str, list]:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    features = fetch_all(CITY_SITES, out_fields=CITY_FIELDS, return_geometry=True, out_sr=4326)
    if not features:
        raise RuntimeError(f"{CITY_SITES.base} returned no sites")
    for feature in sorted(features, key=lambda f: f["attributes"]["OBJECTID"]):
        attrs = feature.get("attributes") or {}
        name = clean(attrs.get("SiteName"))
        guid = str(attrs.get("GlobalID") or "").strip("{} ").lower()
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if not name or not guid or wkt is None:
            continue
        rows["landmark_id"].append(f"ric-ohs-{guid}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(clean(attrs.get("Address")))
        rows["lm_type"].append(LOCAL_TYPE)
        rows["register_number"].append(clean(attrs.get("LastAffectingOrdinance")))
        rows["year_designated"].append(None)
    return rows


def national_register() -> dict[str, list]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USRIC"]
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
            if (clean(attrs.get("County")) or "").lower() != "richmond":
                continue
            if (clean(attrs.get("State")) or "").upper() != "VIRGINIA":
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                continue
            seen.add(ref)
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"ric-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["register_number"].append(ref)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    if not rows["landmark_id"]:
        raise RuntimeError("no National Register listings in Richmond's bounds")
    return rows


def build() -> pa.Table:
    local = city_sites()
    national = national_register()
    data = {k: local[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USRIC"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

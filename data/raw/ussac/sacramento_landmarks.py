#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Sacramento, CA's Register landmarks and National Register listings.

Two registers, both read live from ArcGIS layers:

* **The Sacramento Register of Historic and Cultural Resources**, the city's
  own designation register (City Code chapter 17.604; listing is by council
  ordinance and brings Preservation Commission review), published by the
  city as "Historic Landmarks" (ArcGIS item d24b5b9a87c74e2dbdc0862d758d4471,
  owner `Publisher_SacCity`, the same organisation as the trees), hosted
  layer `Historic_Landmarks/FeatureServer/0`.  The register's districts and
  their contributing buildings are a separate layer ("Historic Resources")
  and are areas, not landmarks; they are not read.  The layer holds 908
  designated *parcels* (polygons) on 2026-11-16: 905 `ASSESSMENT` =
  Landmark and 3 Protected (the I Street Bridge, the Bercut-Richards
  Cannery and one unnamed parcel), each with its APN, address and
  ordinance number.  Only 255 carry a `RESOURCE_NAME`; the other 653 -- almost all of them
  ordinance 3911, the register's founding list of houses -- are recorded by
  address alone and are not published (Eugene's rule: a pin named after its
  street address is not a landmark).  A landmark listed on several parcels
  (Tower Theater, 6; Montgomery Landmarks, 5; St Francis Church, Friary, and
  School, 3) is one row whose geometry is every parcel, keyed by its lowest
  APN as `sac-lm-{APN}` (a landmark on no parcel -- San Francisco Boulevard,
  a street, 2025 -- by its name, `sac-lm-san-francisco-boulevard`).  Names
  are grouped case- and `&`/`and`-blind, which is what joins the two
  spellings of the Libby, McNeil and Libby cannery.
  So 235 Register landmarks (233 Landmark, 2 Protected).  The ordinance is
  `register_number`, and its year (`82-046` -> 1982, `2025-0026` -> 2025) is `year_designated`; the
  founding ordinance `3911` carries no year and gives none.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) within
  `CITY_BOUNDS["USSAC"]` and with `City` = Sacramento (four rows spell it
  `Sacremento`) -- the box also holds West Sacramento and North Highlands.
  Listed buildings, sites and structures, keyed `sac-nr-{NRIS_Refnum}` with
  `CertDate` as `year_designated`; districts are areas and are not published
  (Durham's rule).  NPS data is a US federal work.

A building on both registers keeps both rows, as Columbus does: the two
designations are separate facts, and `lm_type` says which register each row
is from.

The city layer's `licenseInfo` is empty; the City of Sacramento Open Data
Terms of Use govern it, as they do the trees.
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_geometry_to_wkt,
    iter_features,
)
from shared.ingest import CITY_BOUNDS, emit

REGISTER = FeatureLayer(
    "https://services5.arcgis.com/54falWtcpty3V47Z/arcgis/rest/services/"
    "Historic_Landmarks/FeatureServer/0"
)
REGISTER_FIELDS = "OBJECTID,APN,HOUSE,STREET_NAME,STREET_TYPE,ASSESSMENT,RESOURCE_NAME,ORDINANCE"
REGISTER_ASSESSMENTS = {
    "landmark": "Sacramento Register of Historic and Cultural Resources (Landmark)",
    "protected": "Sacramento Register of Historic and Cultural Resources (Protected)",
}

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,City,CertDate,STATUS"
NRHP_CITY_NAMES = frozenset({"sacramento", "sacremento"})
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

_ORDINANCE_YEAR = re.compile(r"^(\d{2}|\d{4})-\d+$")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def ordinance_year(ordinance: str | None) -> int | None:
    """`82-046` -> 1982, `2025-0026` -> 2025, `3911` -> None."""
    match = _ORDINANCE_YEAR.match(ordinance or "")
    if not match:
        return None
    year = match.group(1)
    if len(year) == 4:
        return int(year)
    two = int(year)
    # The register dates from the 1970s; a two-digit year below 50 is 20xx.
    return 2000 + two if two < 50 else 1900 + two


def name_key(name: str) -> str:
    text = name.lower().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text).split())


def address(attrs: dict) -> str | None:
    house = clean(attrs.get("HOUSE"))
    if house == "0":
        house = None
    parts = [house, clean(attrs.get("STREET_NAME")), clean(attrs.get("STREET_TYPE"))]
    return clean(" ".join(p for p in parts if p))


def sacramento_register() -> dict[str, list]:
    features = fetch_all(REGISTER, out_fields=REGISTER_FIELDS, return_geometry=True, out_sr=4326)
    if not features:
        raise RuntimeError(f"{REGISTER.base} returned no landmarks")
    groups: dict[str, list[dict]] = defaultdict(list)
    seen_apns: set[str] = set()
    for feature in features:
        attrs = feature.get("attributes") or {}
        apn = clean(attrs.get("APN"))
        if apn in seen_apns:
            raise RuntimeError(f"Sacramento Register APN {apn} is repeated")
        if apn:
            seen_apns.add(apn)
        assessment = (clean(attrs.get("ASSESSMENT")) or "").lower()
        if assessment not in REGISTER_ASSESSMENTS:
            raise RuntimeError(
                f"Sacramento Register ASSESSMENT {attrs.get('ASSESSMENT')!r} is new; "
                "decide whether it is a designation"
            )
        name = clean(attrs.get("RESOURCE_NAME"))
        if not name:
            continue
        groups[name_key(name)].append(feature)

    rows: dict[str, list] = {k: [] for k in COLUMNS}
    for members in groups.values():
        # Parcels with an APN first, lowest first.
        members.sort(key=lambda f: (f["attributes"]["APN"] is None, f["attributes"]["APN"] or ""))
        first = members[0]["attributes"]
        apn = clean(first.get("APN"))
        key = apn or "-".join(name_key(clean(first["RESOURCE_NAME"])).split())
        rings = [
            ring
            for f in members
            for ring in ((f.get("geometry") or {}).get("rings") or [])
        ]
        wkt = esri_geometry_to_wkt({"rings": rings})
        if wkt is None:
            continue
        ordinance = clean(first.get("ORDINANCE"))
        rows["landmark_id"].append(f"sac-lm-{key}")
        rows["name"].append(clean(first.get("RESOURCE_NAME")))
        rows["geometry_raw"].append(wkt)
        rows["address"].append(address(first))
        rows["lm_type"].append(REGISTER_ASSESSMENTS[clean(first["ASSESSMENT"]).lower()])
        rows["register_number"].append(ordinance)
        rows["year_designated"].append(ordinance_year(ordinance))
    return rows


def national_register() -> dict[str, list]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USSAC"]
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
            if (clean(attrs.get("City")) or "").lower() not in NRHP_CITY_NAMES:
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                continue
            seen.add(ref)
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"sac-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["register_number"].append(ref)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    return rows


def build() -> pa.Table:
    local = sacramento_register()
    national = national_register()
    data = {k: local[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USSAC"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

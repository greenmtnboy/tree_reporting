#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Riverside, CA's designated City Landmarks and National Register listings.

Two registers, both read live from ArcGIS layers:

* **Riverside City Landmarks**, the city's own designation register (Title 20
  of the Municipal Code, Cultural Resources; designated by the City Council on
  the Cultural Heritage Board's recommendation).  Published by the City as
  `Historic_Properties_Designations/FeatureServer/0` on its ArcGIS Online
  (item c0310676be0b427d831b64e19d26c122, owner `rivopendata_admin`), the
  parcel polygons of every designated property: 4,540 rows on 2026-11-20,
  one per property and designation (`designation`).  Only the City Landmark
  rows are read -- `City Landmark` (120), `City Landmark Structures` (2) and
  `City Landmark Object` (2: the Raincross street lights and the citrus
  pergolas on Seventh Street) -- each keyed by the City's property key
  `prop_key` (`riv-lm-3325`; unique over those rows, checked here).  Structures
  of Merit (886), district and Neighborhood Conservation Area contributors
  (about 2,900) are lower-tier or area designations and are not landmarks;
  the county, state and national rows on the same layer duplicate a city
  landmark's parcel or the NPS register below.  A row with no
  `historic_name` (4) is named by its address.  The layer records no
  designation date.  Licence: no named licence; the City's Open Data Policy
  (Administrative Manual 03.016.00, 2017) asks for credit ("Data provided by
  the City of Riverside").
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) within
  `CITY_BOUNDS["USRAL"]` and with `City = Riverside` (the box also holds
  Jurupa Valley and Corona listings).  Listed buildings, sites and
  structures, keyed `riv-nr-{NRIS_Refnum}` with `CertDate` as
  `year_designated`; districts are areas and are not published (Columbus's
  and Seattle's rule).  NPS data is a US federal work.

A building on both registers (the Mission Inn, the Harada House) keeps both
rows, as Seattle and Columbus do, because the two designations are separate
facts; `lm_type` says which register each row is from.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import CITY_BOUNDS, emit

REGISTER = FeatureLayer(
    "https://services.arcgis.com/Fu2oOWg1Aw7azh41/arcgis/rest/services/"
    "Historic_Properties_Designations/FeatureServer/0"
)
REGISTER_FIELDS = "OBJECTID,prop_key,historic_name,designation,rsrc_num,rsrc_name,rsrc_suff"
LANDMARK_DESIGNATIONS = ("City Landmark", "City Landmark Structures", "City Landmark Object")

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,City,CertDate,STATUS"

LOCAL_TYPE = "Riverside City Landmark"
NATIONAL_TYPE = "National Register of Historic Places"

# The register should hold about 124 landmark rows; far fewer means the
# `designation` vocabulary changed under the filter.
MIN_LOCAL_ROWS = 90

COLUMNS = ("landmark_id", "name", "geometry_raw", "address", "lm_type", "register_number", "year_designated")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def street_address(attrs: dict) -> str | None:
    """`3415 Ramona Dr` from the register's number, street and suffix columns."""
    parts = [clean(attrs.get(k)) for k in ("rsrc_num", "rsrc_name", "rsrc_suff")]
    words = " ".join(p for p in parts if p)
    return words.title() if words else None


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def riverside_register() -> dict[str, list]:
    quoted = ", ".join(f"'{d}'" for d in LANDMARK_DESIGNATIONS)
    features = fetch_all(
        REGISTER,
        out_fields=REGISTER_FIELDS,
        where=f"designation IN ({quoted})",
        return_geometry=True,
        out_sr=4326,
    )
    if len(features) < MIN_LOCAL_ROWS:
        raise RuntimeError(
            f"{REGISTER.base} returned {len(features)} City Landmark rows; "
            f"expected at least {MIN_LOCAL_ROWS}"
        )
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    seen: set[str] = set()
    for feature in sorted(features, key=lambda f: f["attributes"]["OBJECTID"]):
        attrs = feature.get("attributes") or {}
        key = attrs.get("prop_key")
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        address = street_address(attrs)
        name = clean(attrs.get("historic_name")) or address
        if key is None or wkt is None or not name:
            continue
        key = str(int(key))
        if key in seen:
            raise RuntimeError(f"Riverside landmark property key {key} is repeated")
        seen.add(key)
        rows["landmark_id"].append(f"riv-lm-{key}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(address)
        rows["lm_type"].append(LOCAL_TYPE)
        rows["register_number"].append(None)
        rows["year_designated"].append(None)
    return rows


def national_register() -> dict[str, list]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USRAL"]
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
            if (clean(attrs.get("City")) or "").lower() != "riverside":
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                continue
            seen.add(ref)
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"riv-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["register_number"].append(ref)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    return rows


def build() -> pa.Table:
    local = riverside_register()
    national = national_register()
    data = {k: local[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    print(
        f"Riverside landmarks: {len(local['landmark_id'])} City Landmarks, "
        f"{len(national['landmark_id'])} National Register listings",
        file=sys.stderr,
    )
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USRAL"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

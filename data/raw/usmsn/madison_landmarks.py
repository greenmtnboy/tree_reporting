#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Madison, WI's designated landmarks and National Register listings.

Two registers, both read live from ArcGIS layers:

* **Madison Local Landmarks**, the landmarks designated by the City's
  Landmarks Commission (Madison General Ordinances ch. 41), published by
  Planning as `Public/OPEN_DATA_PLANNING/MapServer/7` on
  maps.cityofmadison.com (ArcGIS item 279a5ad5f2f94d0bb20f898c523d5b5a,
  "Local Landmarks", owner `CityOfMadison`; the layer behind the City's
  "Historic Districts and Landmarks" app).  183 building or site footprints on
  2026-11-19, each with the historical name, address, the landmark number
  (`ID`), designation date, construction date and `LM_TYPE` (Structure,
  Effigy Mounds, Area).  The landmark number is the key (`msn-lm-12`); the
  one number on two rows, 33, is Forest Hill Cemetery's two parts, which the
  names label "(33A)" and "(33B)", and those take `msn-lm-33a`/`msn-lm-33b`.
  `LLM_Date` gives `year_designated` (null on the four newest rows),
  `CONST_DATE` (text: "1857-58", "c.1855") the first four-digit year as
  `year_built`, and `LM_TYPE` is `property_type`.  The same City Open Data
  terms as the trees apply (MGO 3.72(3)(e): no licence requirement or
  restriction on use; identify the source).  Local historic districts
  (layer 12) are areas, not landmarks, and are not read.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) within
  `CITY_BOUNDS["USMSN"]` and with `City = Madison` -- the box also holds
  Monona, Middleton, Maple Bluff, Shorewood Hills and Fitchburg listings.
  Listed buildings, sites and structures, keyed `msn-nr-{NRIS_Refnum}` with
  `CertDate` as `year_designated`; districts are areas and are not published.
  NPS data is a US federal work.

Many buildings are on both registers; both rows are kept, as Columbus keeps
them, because the two designations are separate facts.  `lm_type` says which
register each row is from.
"""

import re
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_geometry_to_wkt,
    esri_ms_to_date,
    iter_features,
)
from shared.ingest import CITY_BOUNDS, emit

REGISTER = FeatureLayer(
    "https://maps.cityofmadison.com/arcgis/rest/services/Public/OPEN_DATA_PLANNING/MapServer/7"
)
REGISTER_FIELDS = "OBJECTID,ID,HOUSE_NO,ST_DIR,ST_NAME,HIST_NAME,CONST_DATE,LLM,LLM_Date,LM_TYPE"

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,City,CertDate,STATUS"

LOCAL_TYPE = "Madison Landmark"
NATIONAL_TYPE = "National Register of Historic Places"

# "Forest Hill Cemetery (33A)": the part letter a shared landmark number takes.
PART_SUFFIX = re.compile(r"\((\d+)([A-Za-z])\)\s*$")
YEAR = re.compile(r"\b(1[6-9]\d\d|20\d\d)\b")

COLUMNS = (
    "landmark_id",
    "name",
    "geometry_raw",
    "address",
    "lm_type",
    "property_type",
    "register_number",
    "year_built",
    "year_designated",
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def address_of(attrs: dict) -> str | None:
    return clean(" ".join(clean(attrs.get(k)) or "" for k in ("HOUSE_NO", "ST_DIR", "ST_NAME")))


def madison_register() -> dict[str, list]:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    features = fetch_all(REGISTER, out_fields=REGISTER_FIELDS, return_geometry=True, out_sr=4326)
    if not features:
        raise RuntimeError(f"{REGISTER.base} returned no landmarks")
    numbers = Counter((f.get("attributes") or {}).get("ID") for f in features)
    seen: set[str] = set()
    for feature in sorted(features, key=lambda f: f["attributes"]["OBJECTID"]):
        attrs = feature.get("attributes") or {}
        name = clean(attrs.get("HIST_NAME"))
        number = attrs.get("ID")
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if not name or number is None or wkt is None:
            continue
        if (clean(attrs.get("LLM")) or "").lower() != "yes":
            continue
        key = str(int(number))
        if numbers[number] > 1:
            part = PART_SUFFIX.search(name)
            if part is None or part.group(1) != key:
                raise RuntimeError(
                    f"Madison landmark number {key} is repeated and {name!r} "
                    "names no part letter"
                )
            key = f"{key}{part.group(2).lower()}"
        if key in seen:
            raise RuntimeError(f"Madison landmark id {key} is repeated")
        seen.add(key)
        designated = esri_ms_to_date(attrs.get("LLM_Date"))
        built = YEAR.search(clean(attrs.get("CONST_DATE")) or "")
        rows["landmark_id"].append(f"msn-lm-{key}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(address_of(attrs))
        rows["lm_type"].append(LOCAL_TYPE)
        rows["property_type"].append(clean(attrs.get("LM_TYPE")))
        rows["register_number"].append(key.upper())
        rows["year_built"].append(int(built.group(1)) if built else None)
        rows["year_designated"].append(designated.year if designated else None)
    return rows


def national_register() -> dict[str, list]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USMSN"]
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
            if (clean(attrs.get("City")) or "").lower() != "madison":
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                continue
            seen.add(ref)
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"msn-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["property_type"].append(clean(attrs.get("ResType")))
            rows["register_number"].append(ref)
            rows["year_built"].append(None)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    return rows


def build() -> pa.Table:
    local = madison_register()
    national = national_register()
    data = {k: local[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USMSN"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "property_type": pa.array(data["property_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_built": pa.array(data["year_built"], type=pa.int64()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Pittsburgh, PA's City Designated Historic Sites and National Register listings.

Two registers, both read live from ArcGIS layers:

* **City Designated Individual Historic Sites**, the city's own designation
  register (Historic Review Commission; Zoning Code ch. 1101), published by
  the City of Pittsburgh as the hosted layer
  `PGHWEBCHDIndividialProperties/FeatureServer/0` (ArcGIS item
  6d406961bad840078dd9335b6c4d5385, owner `pgh.admin`), mirrored on the WPRDC
  as `city-designated-individual-historic-sites`.  **No licence is stated**:
  the item's `licenseInfo` is a use-constraints and liability disclaimer, and
  the WPRDC package says "License not specified".  157 parcel polygons on
  2026-11-16, each with the site's name and address, and no designation date
  or register number.
  - `historic_i` is the name cut to 50 characters with a trailing `*`;
    `name` is the full name and is preferred.
  - 13 rows are named `Temp - ...` (Pittsburgh Public Schools buildings and
    one address): sites under a pending nomination's temporary protection,
    not designated.  They are not published.
  - A site spread over several parcels repeats its name (Iron City Brewery on
    four, Baywood on two); one row per name is published, the largest parcel.
  - The rows carry no stable key of their own (`lotblock` repeats and is
    blank on bridges), so the key is the name, slugged: `pit-hs-{slug}`.
  The register's districts (`city-designated-historic-districts`) are areas,
  not landmarks, and are not read.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) within
  `CITY_BOUNDS["USPIT"]` and with `City` = Pittsburgh (or "Pittsburgh City");
  the box also holds listings in Bellevue, Millvale, Wilkinsburg, Homestead
  and the other boroughs around the city.  Listed buildings, sites and
  structures, keyed `pit-nr-{NRIS_Refnum}` with `CertDate` as
  `year_designated`; districts are areas and are not published.  NPS data is a
  US federal work.

Many buildings are on both registers; both rows are kept, as Columbus keeps
them, because the two designations are separate facts.  `lm_type` says which
register each row is from.
"""

import re
import sys
import unicodedata
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import CITY_BOUNDS, emit

REGISTER = FeatureLayer(
    "https://services1.arcgis.com/YZCmUqbcsUpOKfj7/arcgis/rest/services/"
    "PGHWEBCHDIndividialProperties/FeatureServer/0"
)
REGISTER_FIELDS = "OBJECTID,historic_i,name,address,Shape__Area"

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,City,CertDate,STATUS"
NRHP_CITY_NAMES = frozenset({"pittsburgh", "pittsburgh city"})

LOCAL_TYPE = "City of Pittsburgh Designated Historic Site"
NATIONAL_TYPE = "National Register of Historic Places"

_TEMPORARY = re.compile(r"^temp\s*-", re.IGNORECASE)

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


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def site_name(attrs: dict) -> str | None:
    full = clean(attrs.get("name"))
    short = clean(attrs.get("historic_i"))
    name = full or short
    if name is None:
        return None
    # The truncated column's marker, when only it was filled in.
    return name.rstrip("*").strip() or None


def pittsburgh_register() -> dict[str, list]:
    features = fetch_all(
        REGISTER, out_fields=REGISTER_FIELDS, return_geometry=True, out_sr=4326
    )
    if not features:
        raise RuntimeError(f"{REGISTER.base} returned no sites")

    best: dict[str, tuple[float, dict, str]] = {}
    temporary = unnamed = 0
    for feature in features:
        attrs = feature.get("attributes") or {}
        name = site_name(attrs)
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if name is None or wkt is None:
            unnamed += 1
            continue
        if _TEMPORARY.match(name):
            temporary += 1
            continue
        key = slug(name)
        area = float(attrs.get("Shape__Area") or 0.0)
        if key not in best or area > best[key][0]:
            best[key] = (area, attrs, wkt)
    print(
        f"Pittsburgh register: {len(features)} rows, {len(best)} sites published, "
        f"{temporary} under temporary protection, {unnamed} without a name or shape",
        file=sys.stderr,
    )

    rows: dict[str, list] = {k: [] for k in COLUMNS}
    for key in sorted(best):
        _, attrs, wkt = best[key]
        rows["landmark_id"].append(f"pit-hs-{key}")
        rows["name"].append(site_name(attrs))
        rows["geometry_raw"].append(wkt)
        rows["address"].append(clean(attrs.get("address")))
        rows["lm_type"].append(LOCAL_TYPE)
        rows["register_number"].append(None)
        rows["year_designated"].append(None)
    return rows


def national_register() -> dict[str, list]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USPIT"]
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
            rows["landmark_id"].append(f"pit-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["register_number"].append(ref)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    if not seen:
        raise RuntimeError("no National Register listings inside Pittsburgh's bounds")
    return rows


def build() -> pa.Table:
    local = pittsburgh_register()
    national = national_register()
    data = {k: local[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USPIT"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

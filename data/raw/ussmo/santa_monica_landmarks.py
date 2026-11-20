#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Santa Monica, CA's designated Landmarks and Structures of Merit, and its
National Register listings.

Two registers, both read live from ArcGIS layers:

* **Historic Landmarks and Structures of Merit**, the city's own register
  under the Landmarks and Historic District Ordinance (1976), designated by
  the Landmarks Commission.  Layer 0 of `Historic_Preservation_Layers/
  MapServer` on the city's ArcGIS Server, gis.santamonica.gov (ArcGIS Online
  item abe20ceaf96d4f8bbb5774d6aa230563, owner `smgov_agoladmin`), published
  under the Open Data Commons Attribution License v1.0 as the item states.
  158 points on 2026-11-19: 143 `Designated City Landmark`, 14 `Structure of
  Merit` and one with no type (a demolished house).  Rows the layer's `info`
  marks as gone (`Demolished`, `Demolition ordered 1994`, `Dead and Removed`
  -- five rows) are not published.  The layer has no landmark number, and
  its `objectid` is the publishing service's row number, so a row is keyed
  by its own name and address (`smo-lm-1130-lincoln-blvd-miles-playhouse`).
  One house (2323 5th St) is in the layer twice with identical values (a
  repeat of an earlier row's name, address and date is dropped), and the
  Miramar Moreton Bay Fig Tree twice, once at "Ocean Ave & Wilshire Blvd"
  and again, 100 m away, at "101 Wilshire Blvd" (`REPEATED_ENTRIES`; the
  first is kept).  Not "same name and date nearby": two different
  Mid-Century Modern buildings at 2002 and 2008 21st St, 15 m apart, share
  both.  `desig_date` (a `m/d/yyyy` string, or
  a bare year) gives `year_designated`.  The district boundaries and the
  Historic Resources Inventory (layers 3 and 2) are areas or surveys, not
  designations of a single place, and are not read.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) within
  `CITY_BOUNDS["USSMO"]` and with `City = Santa Monica`; the box also holds
  Venice, Brentwood and Pacific Palisades listings, which are Los Angeles'.
  Listed buildings, sites and structures, keyed `smo-nr-{NRIS_Refnum}` with
  `CertDate` as `year_designated`; districts are areas and are not published
  (Columbus's and Seattle's rule).  NPS data is a US federal work.

A building on both registers keeps both rows, as Seattle and Columbus do,
because the two designations are separate facts.  `lm_type` says which
register each row is from; `register_number` is the NRIS reference number
(null for the city's register, which numbers nothing).
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import CITY_BOUNDS, emit, make_point_wkt

REGISTER = FeatureLayer(
    "https://gis.santamonica.gov/server/rest/services/Historic_Preservation_Layers/MapServer/0"
)
REGISTER_FIELDS = "objectid,name,addr,desig_date,type,info"

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,City,CertDate,STATUS"

LOCAL_TYPES = frozenset({"Designated City Landmark", "Structure of Merit"})
NATIONAL_TYPE = "National Register of Historic Places"

# `info` notes that say the landmark no longer stands (lower-cased prefixes).
GONE_PREFIXES = ("demolish", "demolition", "dead and removed")

# (name, address) of rows that map a landmark already in the layer under
# another address (lower-cased).
REPEATED_ENTRIES = frozenset({("miramar moreton bay fig tree", "101 wilshire blvd")})

COLUMNS = (
    "landmark_id",
    "name",
    "geometry_raw",
    "address",
    "lm_type",
    "register_number",
    "year_designated",
)

_DATE = re.compile(r"^(?:\d{1,2}/\d{1,2}/)?(\d{4})$")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return None if text in ("", "---") else text


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def designation_year(value) -> int | None:
    text = clean(value)
    if text is None:
        return None
    match = _DATE.match(text)
    if match is None:
        raise RuntimeError(f"Santa Monica landmark desig_date {text!r} is not m/d/yyyy")
    return int(match.group(1))


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def city_register() -> dict[str, list]:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    features = fetch_all(
        REGISTER, out_fields=REGISTER_FIELDS, return_geometry=True, out_sr=4326,
        order_by="objectid",
    )
    if not features:
        raise RuntimeError(f"{REGISTER.base} returned no landmarks")
    kept: set[tuple[str, str | None, int | None]] = set()
    ids: set[str] = set()
    for feature in sorted(features, key=lambda f: f["attributes"]["objectid"]):
        attrs = feature.get("attributes") or {}
        geom = feature.get("geometry") or {}
        name = clean(attrs.get("name"))
        lm_type = clean(attrs.get("type"))
        info = (clean(attrs.get("info")) or "").lower()
        wkt = make_point_wkt(geom.get("x"), geom.get("y"))
        if not name or wkt is None or lm_type not in LOCAL_TYPES:
            continue
        if info.startswith(GONE_PREFIXES):
            continue
        year = designation_year(attrs.get("desig_date"))
        address = clean(attrs.get("addr"))
        if (name.lower(), (address or "").lower()) in REPEATED_ENTRIES:
            continue
        entry = (name.lower(), (address or "").lower(), year)
        if entry in kept:
            continue
        kept.add(entry)
        landmark_id = f"smo-lm-{slug(f'{address or ''} {name}')}"
        if landmark_id in ids:
            raise RuntimeError(f"Santa Monica landmark id {landmark_id} is repeated")
        ids.add(landmark_id)
        rows["landmark_id"].append(landmark_id)
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(address)
        rows["lm_type"].append(f"Santa Monica {lm_type}")
        rows["register_number"].append(None)
        rows["year_designated"].append(year)
    return rows


def national_register() -> dict[str, list]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USSMO"]
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
            if (clean(attrs.get("City")) or "").lower() != "santa monica":
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                continue
            seen.add(ref)
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"smo-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["register_number"].append(ref)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    return rows


def build() -> pa.Table:
    local = city_register()
    national = national_register()
    data = {k: local[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USSMO"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

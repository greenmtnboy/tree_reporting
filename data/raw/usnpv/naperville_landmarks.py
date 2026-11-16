#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Naperville, IL's heritage-plaque buildings and National Register listings.

Naperville publishes no layer of its locally designated landmarks (the
city's `Landmarks` service is a 2017 map tour of 11 points, and its
`HistoricDistrict` service is a leaf-collection layer misnamed), so this
reads the two registers that are published, both live ArcGIS layers:

* **The Naperville Heritage Society's Historic Structures Plaque Program**,
  mapped by the city on its own ArcGIS Online organisation (the one that
  hosts the trees): `NapervilleHeritagePlaqueMap/FeatureServer/0`, "Heritage
  Plaque Location" (item ae8ca80aee364223aa71dd09eecdfbc3).  83 points on
  2026-11-16, every one `Plaque = Yes`, each with the historic name
  (`Benjamin Beidelman House`, `Old Main`, `Martin Mitchell Mansion`), the
  street address, the year built and the district it sits in (39 in the
  Local Historic District, 20 in the Federal one, 5 at Naper Settlement).
  The layer has no register number or GlobalID, so `OBJECTID` is the key
  (`npv-hp-{OBJECTID}`).  `YearBuilt` is text; its first year is taken
  (`1845, 1910` -> 1845).  The item states no licence; it is public on the
  city's organisation alongside the CC0 tree inventory.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) within
  `CITY_BOUNDS["USNPV"]` and with `City = Naperville` -- the box also holds
  listings in Aurora and Romeoville.  That is one building today, Pine Craig
  (75002076, listed 1975), keyed `npv-nr-{NRIS_Refnum}` with `CertDate` as
  `year_designated`; the point is NRIS's own, accurate to tens of metres.  The Naperville Historic District listing is an area,
  not a landmark, and is not published (Durham's rule).

The plaque layer's two district layers (1 and 2) are areas too and are not
read.  The two registers overlap on one building: Pine Craig is the Martin
Mitchell Mansion at Naper Settlement (plaque 64, 260 History Ln), whose
plaque point stands about 90 m from the NRIS point.  Both rows are kept, as
Naples keeps a building on two registers, because the two designations are
separate facts; `lm_type` says which each is.  84 rows in all.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import CITY_BOUNDS, emit, make_point_wkt

PLAQUES = FeatureLayer(
    "https://services1.arcgis.com/rXJ6QApc2sOtl1Pd/arcgis/rest/services/"
    "NapervilleHeritagePlaqueMap/FeatureServer/0"
)
PLAQUE_FIELDS = "OBJECTID,FULL_ADDRE,HistName,YearBuilt,District,Plaque"

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,City,CertDate,STATUS"

PLAQUE_TYPE = "Naperville Heritage Society plaque"
NATIONAL_TYPE = "National Register of Historic Places"

DIRECTIONS = frozenset({"N", "S", "E", "W", "NE", "NW", "SE", "SW"})

COLUMNS = (
    "landmark_id",
    "name",
    "geometry_raw",
    "address",
    "neighborhood",
    "year_built",
    "lm_type",
    "register_number",
    "year_designated",
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def format_address(raw) -> str | None:
    """`8 N SLEIGHT ST` -> `8 N Sleight St`."""
    text = clean(raw)
    if text is None:
        return None
    return " ".join(
        w.lower() if w[0].isdigit() else (w.upper() if w.upper() in DIRECTIONS else w.capitalize())
        for w in text.split()
    )


def first_year(value) -> int | None:
    """The first four-digit run in *value*: `1845, 1910` -> 1845."""
    digits = ""
    for ch in str(value or ""):
        if ch.isdigit():
            digits += ch
            if len(digits) == 4:
                return int(digits)
        else:
            digits = ""
    return None


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def plaques() -> dict[str, list]:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    features = fetch_all(PLAQUES, out_fields=PLAQUE_FIELDS, return_geometry=True, out_sr=4326)
    if not features:
        raise RuntimeError(f"{PLAQUES.base} returned no plaques")
    for feature in sorted(features, key=lambda f: f["attributes"]["OBJECTID"]):
        attrs = feature.get("attributes") or {}
        geom = feature.get("geometry") or {}
        name = clean(attrs.get("HistName"))
        wkt = make_point_wkt(geom.get("x"), geom.get("y"))
        if not name or wkt is None:
            continue
        rows["landmark_id"].append(f"npv-hp-{attrs['OBJECTID']}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(format_address(attrs.get("FULL_ADDRE")))
        rows["neighborhood"].append(clean(attrs.get("District")))
        rows["year_built"].append(first_year(attrs.get("YearBuilt")))
        rows["lm_type"].append(PLAQUE_TYPE)
        rows["register_number"].append(None)
        rows["year_designated"].append(None)
    return rows


def national_register() -> dict[str, list]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USNPV"]
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
            if (clean(attrs.get("City")) or "").lower() != "naperville":
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                continue
            seen.add(ref)
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"npv-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["neighborhood"].append(None)
            rows["year_built"].append(None)
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["register_number"].append(ref)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    return rows


def build() -> pa.Table:
    local = plaques()
    national = national_register()
    data = {k: local[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USNPV"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "neighborhood": pa.array(data["neighborhood"], type=pa.string()),
            "year_built": pa.array(data["year_built"], type=pa.int64()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

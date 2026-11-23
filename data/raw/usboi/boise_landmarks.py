#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Boise, Idaho's historic landmarks: the City's register, topped up from the NPS.

Two registers, both read live from ArcGIS layers:

* **The City of Boise's "Historic Landmarks"** (ArcGIS item
  b44aba318fd746988f1a48887bb0f990, owner `Boise_GIS`, the hosted layer
  `HistoricLandmarks/FeatureServer/0`): "sites designated by the City of Boise
  as historic local landmarks and sites registered as historic by the
  National Register of Historic Places (NRHP) within Boise's Area of Impact".
  161 building polygons on 2026-11-22, each with its name, address, the two
  flags `LocalSite` / `NationalSite`, `YearDesignated` and the NRHP reference
  number.  Licence: the City's open-data site states CC BY 4.0 for its
  content (https://opendata.cityofboise.org/; the item's own `licenseInfo` is
  only the no-warranty disclaimer).
  - `Status = Demolished` (20 rows) is not published; `Relocated` (5) is --
    the polygon is where the building stands now.
  - `SiteType = District` (3) is an area, not a landmark, and is not
    published (Durham's rule).
  - The rows carry no stable key: `OBJECTID` is a dense block (689,039..
    689,199, a layer reloaded whole) and an NRHP number is shared by several
    buildings of one listing.  Every `SiteName` is distinct, so the key is the
    name, slugged: `boise-lm-{slug}`.
  - `lm_type` says which register(s) list the site.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) within
  `CITY_BOUNDS["USBOI"]` and with `City = Boise`, for the listings the city
  layer does not carry (recent listings: 4 buildings on 2026-11-22).  A
  listing is taken as already carried when its NRIS number is on a city row
  or its point lies on a city building's envelope (districts aside) (the city layer mistypes one
  number, `09001415` for the O'Farrell cabin's `99001415`).  Listed
  buildings, sites, structures and objects, keyed `boise-nr-{NRIS_Refnum}`
  with `CertDate` as `year_designated`; districts are not published.  NPS
  data is a US federal work.
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
    "https://services1.arcgis.com/WHM6qC35aMtyAAlN/arcgis/rest/services/"
    "HistoricLandmarks/FeatureServer/0"
)
REGISTER_FIELDS = (
    "OBJECTID,SiteName,NationalSite,LocalSite,YearDesignated,"
    "NationalRegistrationNumber,PrimaryAddress,Status,SiteType"
)

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,City,CertDate,STATUS"

LOCAL_TYPE = "City of Boise Historic Landmark"
NATIONAL_TYPE = "National Register of Historic Places"
BOTH_TYPE = "City of Boise Historic Landmark and National Register of Historic Places"

# A city polygon's envelope is widened by this much (degrees, ~20 m) when
# asking whether an NPS point is the same site.
ENVELOPE_PAD = 0.0002

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


def nris(value) -> str | None:
    """An NRIS reference number as the NPS writes it (8 digits, or 9 since 2015)."""
    if value in (None, ""):
        return None
    return str(int(value)).zfill(8)


def envelope(geometry: dict | None) -> tuple[float, float, float, float] | None:
    points = [pt for ring in (geometry or {}).get("rings") or [] for pt in ring]
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def boise_register(rows: dict[str, list]) -> tuple[set[str], list]:
    features = fetch_all(REGISTER, out_fields=REGISTER_FIELDS, return_geometry=True, out_sr=4326)
    if not features:
        raise RuntimeError(f"{REGISTER.base} returned no sites")
    refs: set[str] = set()
    boxes: list = []
    seen: set[str] = set()
    skipped = {"demolished": 0, "district": 0, "unnamed": 0}
    for feature in features:
        attrs = feature.get("attributes") or {}
        ref = nris(attrs.get("NationalRegistrationNumber"))
        if ref:
            refs.add(ref)  # a demolished listing is still not an NPS gap
        box = envelope(feature.get("geometry"))
        # A district's envelope covers many buildings it does not name.
        if box and (clean(attrs.get("SiteType")) or "").lower() != "district":
            boxes.append(box)
        name = clean(attrs.get("SiteName"))
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if name is None or wkt is None or slug(name) in seen:
            skipped["unnamed"] += 1
            continue
        if (clean(attrs.get("Status")) or "").lower() == "demolished":
            skipped["demolished"] += 1
            continue
        if (clean(attrs.get("SiteType")) or "").lower() == "district":
            skipped["district"] += 1
            continue
        seen.add(slug(name))
        local = clean(attrs.get("LocalSite")) == "Y"
        national = clean(attrs.get("NationalSite")) == "Y"
        year = attrs.get("YearDesignated")
        rows["landmark_id"].append(f"boise-lm-{slug(name)}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(clean(attrs.get("PrimaryAddress")))
        rows["lm_type"].append(
            BOTH_TYPE if local and national else LOCAL_TYPE if local else NATIONAL_TYPE
        )
        rows["register_number"].append(ref)
        rows["year_designated"].append(int(year) if year else None)
    print(
        f"Boise register: {len(features)} rows, {len(seen)} published, skipped {skipped}",
        file=sys.stderr,
    )
    return refs, boxes


def on_a_city_site(geometry: dict | None, boxes: list) -> bool:
    x, y = (geometry or {}).get("x"), (geometry or {}).get("y")
    if x is None or y is None:
        return False
    return any(
        x0 - ENVELOPE_PAD <= x <= x1 + ENVELOPE_PAD and y0 - ENVELOPE_PAD <= y <= y1 + ENVELOPE_PAD
        for x0, y0, x1, y1 in boxes
    )


def national_register(rows: dict[str, list], city_refs: set[str], boxes: list) -> None:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USBOI"]
    box = {
        "geometry": f"{lon_min},{lat_min},{lon_max},{lat_max}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }
    seen: set[str] = set()
    added = carried = 0
    for layer in NRHP_LAYERS:
        for feature in fetch_all(
            layer, out_fields=NRHP_FIELDS, return_geometry=True, out_sr=4326, extra_params=box
        ):
            attrs = feature.get("attributes") or {}
            ref = clean(attrs.get("NRIS_Refnum"))
            name = clean(attrs.get("RESNAME"))
            geometry = feature.get("geometry")
            wkt = esri_geometry_to_wkt(geometry)
            if not ref or not name or wkt is None or ref in seen:
                continue
            if (clean(attrs.get("City")) or "").lower() != "boise":
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                continue
            seen.add(ref)
            if ref in city_refs or on_a_city_site(geometry, boxes):
                carried += 1
                continue
            added += 1
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"boise-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["register_number"].append(ref)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    if not seen:
        raise RuntimeError("no National Register listings inside Boise's bounds")
    print(
        f"Boise NRHP: {len(seen)} listings in bounds, {carried} already on the city "
        f"register, {added} added",
        file=sys.stderr,
    )


def build() -> pa.Table:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    refs, boxes = boise_register(rows)
    national_register(rows, refs, boxes)
    n = len(rows["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(rows["landmark_id"], type=pa.string()),
            "name": pa.array(rows["name"], type=pa.string()),
            "city": pa.array(["USBOI"] * n, type=pa.string()),
            "geometry_raw": pa.array(rows["geometry_raw"], type=pa.string()),
            "address": pa.array(rows["address"], type=pa.string()),
            "lm_type": pa.array(rows["lm_type"], type=pa.string()),
            "register_number": pa.array(rows["register_number"], type=pa.string()),
            "year_designated": pa.array(rows["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

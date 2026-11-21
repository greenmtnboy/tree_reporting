#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Rochester, NY's National Register of Historic Places listings.

**The National Register of Historic Places** (the National Park Service's
`cultural_resources/nrhp_locations` MapServer, points then polygons) inside
the City of Rochester's boundary.  NPS data is a US federal work.  Listed
buildings, sites, structures and objects are published, keyed
`roc-nr-{NRIS_Refnum}` with `CertDate` as `year_designated`; districts are
areas and are not published (Durham's rule), and neither is a listing whose
`STATUS` is not `Listed`.

The `City` column is a mailing address -- listings in Brighton and Gates
read Rochester -- so the filter is the City's own boundary
(`Hosted/City_Boundary/FeatureServer/0` on the City's ArcGIS Server, three
polygons, the City of Rochester's municipal boundary), tested against each
listing's point (a polygon listing: the mean of its outer ring).

The City's own register -- the landmarks its Preservation Board designates
-- is not published as data: the City publishes only
its eight Preservation Districts (`Hosted/Preservation_Districts`), which are
areas, and no layer of individually designated landmarks.
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

CITY_BOUNDARY = FeatureLayer(
    "https://maps.cityofrochester.gov/server/rest/services/Hosted/"
    "City_Boundary/FeatureServer/0",
    timeout=60,
)

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,County,State,CertDate,STATUS"

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


def city_rings() -> list[list[list[float]]]:
    """Every ring of the City boundary in WGS84 (even-odd: holes are rings too)."""
    features = fetch_all(
        CITY_BOUNDARY, out_fields="objectid", order_by="objectid", return_geometry=True, out_sr=4326
    )
    rings = [ring for f in features for ring in ((f.get("geometry") or {}).get("rings") or [])]
    if not rings:
        raise RuntimeError(f"{CITY_BOUNDARY.base} returned no boundary")
    return rings


def inside(x: float, y: float, rings: list[list[list[float]]]) -> bool:
    """Even-odd point-in-polygon over all rings."""
    hit = False
    for ring in rings:
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                hit = not hit
    return hit


def anchor(geometry: dict | None) -> tuple[float, float] | None:
    if not geometry:
        return None
    if "x" in geometry and "y" in geometry:
        try:
            return float(geometry["x"]), float(geometry["y"])
        except (TypeError, ValueError):
            return None
    rings = geometry.get("rings") or []
    if rings and rings[0]:
        ring = rings[0]
        return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)
    return None


def national_register() -> dict[str, list]:
    rings = city_rings()
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USROC"]
    pad = 0.01  # the boundary reaches a little past the box's west edge
    box = {
        "geometry": f"{lon_min - pad},{lat_min - pad},{lon_max + pad},{lat_max + pad}",
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
            geometry = feature.get("geometry")
            wkt = esri_geometry_to_wkt(geometry)
            point = anchor(geometry)
            if not ref or not name or wkt is None or point is None or ref in seen:
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                continue
            if not inside(point[0], point[1], rings):
                continue
            seen.add(ref)
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"roc-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["register_number"].append(ref)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    if not rows["landmark_id"]:
        raise RuntimeError("no National Register listings inside Rochester's boundary")
    return rows


def build() -> pa.Table:
    data = national_register()
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USROC"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

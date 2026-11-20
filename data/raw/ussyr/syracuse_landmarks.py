#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Syracuse, NY's National Register of Historic Places listings.

**The National Register of Historic Places** (the National Park Service's
`cultural_resources/nrhp_locations` MapServer, points then polygons), kept
where the listing lies inside the City of Syracuse's boundary.  NPS data is a
US federal work.  Listed buildings, sites, structures and objects, keyed
`syr-nr-{NRIS_Refnum}` with `CertDate` as `year_designated`; districts are
areas and are not published (Durham's rule).

The boundary is the City's own `Syracuse_City_Boundary` layer (ArcGIS item
19f7a97333214e76a3bf17b2312befad, owner `admin_syr`), one 400-vertex ring.
`City` = Syracuse is not the filter: it is a mailing address, and DeWitt,
Salina and Geddes listings carry it too.  A polygon listing is tested at its
outer ring's vertex centroid.

**The City's own register is not read.**  "Syracuse Historical Properties"
(item c02afeddb8f349538ce9b13adf72cf82, the Landmark Preservation Board's
list) is 3,556 parcel points with an address, a protected-site flag and an
NR flag but no name: almost all are contributing parcels of districts, and a
landmark needs a name to show.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (  # noqa: E402
    FeatureLayer,
    esri_geometry_to_wkt,
    iter_features,
)
from shared.ingest import CITY_BOUNDS, emit  # noqa: E402

BOUNDARY = FeatureLayer(
    "https://services.arcgis.com/uDTUpUPbk8X8mXwl/arcgis/rest/services/"
    "Syracuse_City_Boundary/FeatureServer/0"
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


def city_rings() -> list[list[tuple[float, float]]]:
    """The City boundary's rings in WGS84, as (lon, lat) lists."""
    rings = [
        [(float(x), float(y)) for x, y, *_ in ring]
        for feature in fetch_all(
            BOUNDARY, out_fields="*", return_geometry=True, out_sr=4326, order_by="FID"
        )
        for ring in (feature.get("geometry") or {}).get("rings") or []
    ]
    if not rings:
        raise RuntimeError(f"{BOUNDARY.base} returned no boundary")
    return rings


def inside(lon: float, lat: float, rings: list[list[tuple[float, float]]]) -> bool:
    """Even-odd ray casting over every ring, so a hole is excluded."""
    hit = False
    for ring in rings:
        n = len(ring)
        for i in range(n):
            x1, y1 = ring[i]
            x2, y2 = ring[(i + 1) % n]
            if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                hit = not hit
    return hit


def anchor(geometry: dict | None) -> tuple[float, float] | None:
    """The point a listing is located by: the point itself, or the vertex
    centroid of a polygon's first ring."""
    if not geometry:
        return None
    if "x" in geometry and "y" in geometry:
        return float(geometry["x"]), float(geometry["y"])
    rings = geometry.get("rings") or []
    if rings and rings[0]:
        ring = rings[0]
        return (
            sum(float(p[0]) for p in ring) / len(ring),
            sum(float(p[1]) for p in ring) / len(ring),
        )
    return None


def national_register() -> dict[str, list]:
    rings = city_rings()
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USSYR"]
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
            geometry = feature.get("geometry")
            wkt = esri_geometry_to_wkt(geometry)
            if not ref or not name or wkt is None or ref in seen:
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                continue
            point = anchor(geometry)
            if point is None or not inside(point[0], point[1], rings):
                continue
            seen.add(ref)
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"syr-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["register_number"].append(ref)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    if not rows["landmark_id"]:
        raise RuntimeError("no National Register listings inside Syracuse's boundary")
    return rows


def build() -> pa.Table:
    data = national_register()
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USSYR"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

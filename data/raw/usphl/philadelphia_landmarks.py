#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Philadelphia's National Register listings, marked where the city also designates them.

Two registers, both read live from ArcGIS layers:

* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) with
  `County = Philadelphia` -- the city and the county are one -- and inside
  `CITY_BOUNDS["USPHL"]`.  The `City` column is not used: it misspells
  Philadelphia on three listings.  Listed buildings, sites and structures are
  published, keyed `phl-nr-{NRIS_Refnum}` with `CertDate` as
  `year_designated`; districts (83) are areas and are not published (Durham's
  rule), and neither is a listing whose `STATUS` is not `Listed`.  NPS data
  is a US federal work.
* **The Philadelphia Register of Historic Places**, the Historical
  Commission's register, published as `Historic_sites_PhilReg` on the City's
  ArcGIS Online organisation (item 50328dce4d49429baa0122df6ea5f949, owner
  `maps.phl.data`; OpenDataPhilly "Philadelphia Registered Historic
  Properties", "City of Philadelphia License").  It is a parcel layer --
  14,988 polygons on 2026-11-16, most of them contributing properties of the
  city's historic districts -- with an address (`loc`) and designation dates
  but **no name**.  The 5,629 individually designated parcels (`idesigdate1`
  set) are read, and a National Register listing whose point falls inside one
  is marked as on both registers (`lm_type`), with `year_designated` the
  earlier of the two designations.  A parcel designated only by the city is
  not published on its own: a landmark named after its street address is not
  a landmark (Eugene's rule), and the register has nothing else to name it by.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import CITY_BOUNDS, emit

PHILREG = FeatureLayer(
    "https://services.arcgis.com/fLeGjb7u4uXqeF9q/arcgis/rest/services/"
    "Historic_sites_PhilReg/FeatureServer/0",
    timeout=180,
)
PHILREG_WHERE = "idesigdate1 IS NOT NULL AND idesigdate1 <> ''"

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,County,State,CertDate,STATUS"
NRHP_WHERE = "County = 'Philadelphia' AND State = 'PENNSYLVANIA'"

NATIONAL_TYPE = "National Register of Historic Places"
BOTH_TYPE = "National Register of Historic Places and Philadelphia Register of Historic Places"

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


def designation_year(value) -> int | None:
    """`5/1/1975` -> 1975."""
    match = re.fullmatch(r"\d{1,2}/\d{1,2}/(\d{4})", clean(value) or "")
    return int(match.group(1)) if match else None


def bounding_box() -> dict[str, str]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USPHL"]
    return {
        "geometry": f"{lon_min},{lat_min},{lon_max},{lat_max}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }


def representative_point(geometry: dict | None) -> tuple[float, float] | None:
    """(lon, lat): the point itself, or the mean vertex of a polygon's first ring."""
    if not geometry:
        return None
    if "x" in geometry:
        x, y = geometry.get("x"), geometry.get("y")
        return (float(x), float(y)) if x is not None and y is not None else None
    rings = geometry.get("rings") or []
    if not rings or not rings[0]:
        return None
    ring = rings[0]
    return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)


class Parcels:
    """The individually designated parcels, for point-in-polygon lookups."""

    def __init__(self) -> None:
        self.parcels: list[tuple[tuple[float, float, float, float], list, int | None]] = []
        for page in iter_features(
            PHILREG,
            out_fields="objectid,idesigdate1,idesigdate2",
            where=PHILREG_WHERE,
            return_geometry=True,
            out_sr=4326,
            order_by="objectid",
        ):
            for feature in page:
                rings = (feature.get("geometry") or {}).get("rings") or []
                points = [p for ring in rings for p in ring]
                if not points:
                    continue
                attrs = feature.get("attributes") or {}
                years = [
                    y for y in (designation_year(attrs.get("idesigdate1")),
                                designation_year(attrs.get("idesigdate2")))
                    if y is not None
                ]
                box = (
                    min(p[0] for p in points), min(p[1] for p in points),
                    max(p[0] for p in points), max(p[1] for p in points),
                )
                self.parcels.append((box, rings, min(years) if years else None))
        if not self.parcels:
            raise RuntimeError(f"{PHILREG.base} returned no individually designated parcels")

    def containing(self, lon: float, lat: float) -> tuple[bool, int | None]:
        """(inside a designated parcel, that parcel's designation year)."""
        for (x0, y0, x1, y1), rings, year in self.parcels:
            if x0 <= lon <= x1 and y0 <= lat <= y1 and inside(lon, lat, rings):
                return True, year
        return False, None


def inside(x: float, y: float, rings: list) -> bool:
    """Even-odd ray casting over every ring, so a hole counts as outside."""
    hit = False
    for ring in rings:
        for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
            if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
                hit = not hit
    return hit


def build() -> pa.Table:
    parcels = Parcels()
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    seen: set[str] = set()
    skipped = {"district": 0, "not listed": 0}
    for layer in NRHP_LAYERS:
        for page in iter_features(
            layer,
            out_fields=NRHP_FIELDS,
            where=NRHP_WHERE,
            return_geometry=True,
            out_sr=4326,
            extra_params=bounding_box(),
        ):
            for feature in page:
                attrs = feature.get("attributes") or {}
                ref = clean(attrs.get("NRIS_Refnum"))
                name = clean(attrs.get("RESNAME"))
                geometry = feature.get("geometry")
                wkt = esri_geometry_to_wkt(geometry)
                point = representative_point(geometry)
                if not ref or not name or wkt is None or point is None or ref in seen:
                    continue
                if clean(attrs.get("STATUS")) != "Listed":
                    skipped["not listed"] += 1
                    continue
                if (clean(attrs.get("ResType")) or "").lower() == "district":
                    skipped["district"] += 1
                    continue
                seen.add(ref)
                cert = clean(attrs.get("CertDate")) or ""
                national_year = int(cert[:4]) if cert[:4].isdigit() else None
                both, city_year = parcels.containing(*point)
                years = [y for y in (national_year, city_year) if y is not None]
                rows["landmark_id"].append(f"phl-nr-{ref}")
                rows["name"].append(name)
                rows["geometry_raw"].append(wkt)
                rows["address"].append(clean(attrs.get("Address")))
                rows["lm_type"].append(BOTH_TYPE if both else NATIONAL_TYPE)
                rows["register_number"].append(ref)
                rows["year_designated"].append(min(years) if years else None)
    if not seen:
        raise RuntimeError("no National Register listings read for Philadelphia")
    both = sum(t == BOTH_TYPE for t in rows["lm_type"])
    print(
        f"Philadelphia landmarks: {len(seen)} National Register listings published, "
        f"{both} of them also on the Philadelphia Register "
        f"({len(parcels.parcels)} individually designated parcels read); skipped "
        f"{skipped['district']} districts and {skipped['not listed']} not listed",
        file=sys.stderr,
    )
    n = len(rows["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(rows["landmark_id"], type=pa.string()),
            "name": pa.array(rows["name"], type=pa.string()),
            "city": pa.array(["USPHL"] * n, type=pa.string()),
            "geometry_raw": pa.array(rows["geometry_raw"], type=pa.string()),
            "address": pa.array(rows["address"], type=pa.string()),
            "lm_type": pa.array(rows["lm_type"], type=pa.string()),
            "register_number": pa.array(rows["register_number"], type=pa.string()),
            "year_designated": pa.array(rows["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

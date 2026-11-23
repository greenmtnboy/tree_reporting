#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Salem, Oregon's historic landmarks: the City's register, topped up from the NPS.

Two registers, both read live from ArcGIS layers:

* **The City of Salem's historic buildings inventory** (ArcGIS item
  a49aaabfe16945ad90053f340b2c96bf, owner `SalemGIS`, the hosted layer
  `CD_Historic_Buildings/FeatureServer/0`, "maintained on a continual basis"
  by the Community Development Department): 622 surveyed buildings, of which
  the ones the City lists -- `LOCALREG = Y` (a Salem local landmark, 159) or
  `NATREGIND = Y` (individually listed on the National Register, 84) -- are
  published: 218 points on 2026-11-23.  Keyed `salem-lm-{GISID}` (`GISID`
  is the layer's own building number, non-null and unique over the
  layer); `HISTNAME` is the name, `SHPOID` (the Oregon SHPO inventory number)
  the `register_number`, and the local listing year (`DATELISTLOC`, a year
  or `m/d/yyyy`) or else the National Register one (`DATELISTNR`) is
  `year_designated`.  `lm_type` says which register(s) list the building.
  Licence: resale prohibited by the City; displayed with attribution only,
  not licensed for reuse.  The item's `licenseInfo` and the DataSalem Terms
  of Use (https://data.cityofsalem.net/pages/disclaimer) say "Development of
  derivative products is encouraged however copying and selling the data is
  prohibited" -- the same terms as the tree inventory.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons)
  within `CITY_BOUNDS["USSLM"]` and with `City = Salem`, for the listings the
  City layer does not carry.  The City rows have no NRIS number to match on
  (`NRNMBR` is set on 23 of 218, some truncated), so a listing is taken as
  carried when a published City row lies within `MATCH_METRES` of it, or a
  City row individually listed on the National Register within
  `NR_MATCH_METRES` (the NPS points sit up to ~130 m off the building on a
  large lot such as the Capitol).  Listed buildings, sites, structures and
  objects, keyed `salem-nr-{NRIS_Refnum}` with `CertDate` as
  `year_designated`; districts are areas and are not published (Columbus's
  and Durham's rule).  NPS data is a US federal work.
"""

import math
import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import CITY_BOUNDS, emit

REGISTER = FeatureLayer(
    "https://services.arcgis.com/kIA6yS9KDGqZL7U3/arcgis/rest/services/"
    "CD_Historic_Buildings/FeatureServer/0"
)
REGISTER_FIELDS = "OBJECTID,GISID,SHPOID,ADDRESS,HISTNAME,LOCALREG,NATREGIND,DATELISTLOC,DATELISTNR"
REGISTER_WHERE = "LOCALREG = 'Y' OR NATREGIND = 'Y'"

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,City,CertDate,STATUS"

LOCAL_TYPE = "City of Salem Historic Landmark"
NATIONAL_TYPE = "National Register of Historic Places"
BOTH_TYPE = "City of Salem Historic Landmark and National Register of Historic Places"

# An NPS point within this distance of any published City row is that row.
MATCH_METRES = 20
# ... and within this of a City row the City itself marks as individually
# National-Register-listed.
NR_MATCH_METRES = 150

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


def year_of(value) -> int | None:
    """`1989`, `4/12/2010` or `5/8/1973` -> the year; anything else None."""
    match = re.search(r"\b(1[89]\d\d|20\d\d)\b", clean(value) or "")
    return int(match.group(1)) if match else None


def point_of(geometry: dict | None) -> tuple[float, float] | None:
    if not geometry:
        return None
    if "x" in geometry and geometry.get("x") is not None:
        return float(geometry["x"]), float(geometry["y"])
    points = [pt for ring in geometry.get("rings") or [] for pt in ring]
    if not points:
        return None
    return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)


def metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    dx = (a[0] - b[0]) * 111_320 * math.cos(math.radians((a[1] + b[1]) / 2))
    dy = (a[1] - b[1]) * 110_574
    return math.hypot(dx, dy)


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def salem_register(rows: dict[str, list]) -> list[tuple[tuple[float, float], bool]]:
    """Append the City's listed buildings; return their points and NR flags."""
    features = fetch_all(
        REGISTER, out_fields=REGISTER_FIELDS, where=REGISTER_WHERE,
        return_geometry=True, out_sr=4326,
    )
    if not features:
        raise RuntimeError(f"{REGISTER.base} returned no listed buildings")
    points: list[tuple[tuple[float, float], bool]] = []
    seen: set[int] = set()
    skipped = 0
    for feature in features:
        attrs = feature.get("attributes") or {}
        gisid = attrs.get("GISID")
        name = clean(attrs.get("HISTNAME"))
        geometry = feature.get("geometry")
        wkt = esri_geometry_to_wkt(geometry)
        point = point_of(geometry)
        if gisid is None or name is None or wkt is None or point is None:
            skipped += 1
            continue
        if gisid in seen:
            raise RuntimeError(f"Salem register: GISID {gisid} repeats; it is no longer a key")
        seen.add(gisid)
        local = clean(attrs.get("LOCALREG")) == "Y"
        national = clean(attrs.get("NATREGIND")) == "Y"
        points.append((point, national))
        rows["landmark_id"].append(f"salem-lm-{int(gisid)}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(clean(attrs.get("ADDRESS")))
        rows["lm_type"].append(
            BOTH_TYPE if local and national else LOCAL_TYPE if local else NATIONAL_TYPE
        )
        rows["register_number"].append(clean(attrs.get("SHPOID")))
        rows["year_designated"].append(
            (year_of(attrs.get("DATELISTLOC")) if local else None)
            or year_of(attrs.get("DATELISTNR"))
        )
    print(
        f"Salem register: {len(features)} listed rows, {len(seen)} published, "
        f"{skipped} without a GISID, name or point",
        file=sys.stderr,
    )
    return points


def carried(point: tuple[float, float] | None, city: list) -> bool:
    if point is None:
        return False
    return any(
        metres(point, p) <= (NR_MATCH_METRES if national else MATCH_METRES)
        for p, national in city
    )


def national_register(rows: dict[str, list], city: list) -> None:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USSLM"]
    box = {
        "geometry": f"{lon_min},{lat_min},{lon_max},{lat_max}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }
    seen: set[str] = set()
    added = already = 0
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
            if (clean(attrs.get("City")) or "").lower() != "salem":
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                continue
            if "historic district" in name.lower():
                continue
            seen.add(ref)
            if carried(point_of(geometry), city):
                already += 1
                continue
            added += 1
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"salem-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["register_number"].append(ref)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    if not seen:
        raise RuntimeError("no National Register listings inside Salem's bounds")
    print(
        f"Salem NRHP: {len(seen)} listings in bounds, {already} already on the City "
        f"register, {added} added",
        file=sys.stderr,
    )


def build() -> pa.Table:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    city = salem_register(rows)
    national_register(rows, city)
    n = len(rows["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(rows["landmark_id"], type=pa.string()),
            "name": pa.array(rows["name"], type=pa.string()),
            "city": pa.array(["USSLM"] * n, type=pa.string()),
            "geometry_raw": pa.array(rows["geometry_raw"], type=pa.string()),
            "address": pa.array(rows["address"], type=pa.string()),
            "lm_type": pa.array(rows["lm_type"], type=pa.string()),
            "register_number": pa.array(rows["register_number"], type=pa.string()),
            "year_designated": pa.array(rows["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

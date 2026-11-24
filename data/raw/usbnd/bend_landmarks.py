#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Bend, Oregon's historic landmarks: the City's register, topped up from the NPS.

Two registers, both read live from ArcGIS layers:

* **The City of Bend's Historic Resources** (ArcGIS item
  70b88002805f42dfa94d0c470d49eec1, owner `CityofBendOR`, the hosted layer
  `Historic_Resources_Points/FeatureServer/0`, on data.bendoregon.gov):
  "buildings, historic or cultural sites, structures, or objects that meet
  the significance and integrity criteria for designation as a landmark
  designated by the City Council, State of Oregon or National Park Service"
  (Bend Development Code 10.20), "updated as changes are approved by City
  Council".  65 points on 2026-11-23, of which the three `TAB_NAME =
  District` rows are areas and are not published (Columbus's and Durham's
  rule): 62 resources.  The layer was reloaded in one edit (every row has
  the same `created_date`), so its OBJECTID and GlobalID are not stable;
  the key is the City's own resource number, `HR0002`, which the layer
  carries only as the file name of its photograph (`PIC_URL`
  `.../HistoricResources/HR0002.jpg`) -- `bend-lm-HR0002`, and it is the
  `register_number`.  A row whose photograph does not follow that pattern,
  or a number that repeats, raises.  `DESC1` ("Constructed: 1912-13") is
  `year_built` (the first year); `SHORT_DESC` is the address.
  Licence: no licence stated; displayed with attribution only, not licensed
  for reuse (the item's `licenseInfo` is the City's "for reference purposes
  only ... provided AS IS" disclaimer).
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons)
  within `CITY_BOUNDS["USBND"]` and with `City = Bend`, for the listings the
  City layer does not carry.  Most of Bend's National Register buildings are
  on the City register under another form of the name (`Smith, N. P.,
  Pioneer Hardware Store` is `N.P. Smith Hardware Store`), so a listing is
  taken as carried when a City row lies within `MATCH_METRES` of it, or
  within `NAME_MATCH_METRES` and shares a distinctive word of the name, or
  within `FULL_NAME_MATCH_METRES` and shares two.
  Listed buildings, sites, structures and objects, keyed `bend-nr-{NRIS_Refnum}`
  with `CertDate` as `year_designated`; districts are not published.  NPS
  data is a US federal work.
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
    "https://services5.arcgis.com/JisFYcK2mIVg9ueP/arcgis/rest/services/"
    "Historic_Resources_Points/FeatureServer/0"
)
REGISTER_FIELDS = "OBJECTID,NAME,TAB_NAME,SHORT_DESC,DESC1,PIC_URL"
RESOURCE_NUMBER = re.compile(r"/(HR\d{4})\.jpe?g$", re.IGNORECASE)

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,City,CertDate,STATUS"

LOCAL_TYPE = "City of Bend Historic Resource"
NATIONAL_TYPE = "National Register of Historic Places"

# An NPS point within this distance of a City row is that row ...
MATCH_METRES = 20
# ... and within this one when the two names share a distinctive word.
NAME_MATCH_METRES = 150
# ... and within this one when they share two (the NPS point for `Jones,
# Simpson E., House` sits ~925 m from the City's `Simpson Jones House`).
FULL_NAME_MATCH_METRES = 1500
GENERIC_WORDS = frozenset({
    "bend", "house", "building", "hotel", "school", "church", "store", "site",
    "landmark", "group", "and", "the", "old", "new", "office", "homestead",
})

COLUMNS = (
    "landmark_id",
    "name",
    "geometry_raw",
    "address",
    "lm_type",
    "register_number",
    "year_built",
    "year_designated",
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def year_of(value) -> int | None:
    match = re.search(r"\b(1[6-9]\d\d|20\d\d)\b", clean(value) or "")
    return int(match.group(1)) if match else None


def words(name: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z]+", name.lower().replace("'", ""))
        if len(w) >= 4 and w not in GENERIC_WORDS
    }


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


def bend_register(rows: dict[str, list]) -> list[tuple[tuple[float, float], set[str]]]:
    """Append the City's resources; return their points and name words."""
    features = fetch_all(REGISTER, out_fields=REGISTER_FIELDS, return_geometry=True, out_sr=4326)
    if not features:
        raise RuntimeError(f"{REGISTER.base} returned no historic resources")
    city: list[tuple[tuple[float, float], set[str]]] = []
    seen: set[str] = set()
    districts = skipped = 0
    for feature in features:
        attrs = feature.get("attributes") or {}
        if (clean(attrs.get("TAB_NAME")) or "").lower() == "district":
            districts += 1
            continue
        name = clean(attrs.get("NAME"))
        geometry = feature.get("geometry")
        wkt = esri_geometry_to_wkt(geometry)
        point = point_of(geometry)
        if name is None or wkt is None or point is None:
            skipped += 1
            continue
        match = RESOURCE_NUMBER.search(clean(attrs.get("PIC_URL")) or "")
        if match is None:
            raise RuntimeError(
                f"Bend register: {name!r} has no HR resource number in PIC_URL "
                f"({attrs.get('PIC_URL')!r}); the key has moved"
            )
        number = match.group(1).upper()
        if number in seen:
            raise RuntimeError(f"Bend register: resource number {number} repeats")
        seen.add(number)
        city.append((point, words(name)))
        rows["landmark_id"].append(f"bend-lm-{number}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(clean(attrs.get("SHORT_DESC")))
        rows["lm_type"].append(LOCAL_TYPE)
        rows["register_number"].append(number)
        rows["year_built"].append(year_of(attrs.get("DESC1")))
        rows["year_designated"].append(None)
    print(
        f"Bend register: {len(features)} rows, {len(seen)} published, "
        f"{districts} districts not published, {skipped} without a name or point",
        file=sys.stderr,
    )
    return city


def carried(point: tuple[float, float] | None, name: str, city: list) -> bool:
    if point is None:
        return False
    mine = words(name)
    return any(
        metres(point, p) <= MATCH_METRES
        or (metres(point, p) <= NAME_MATCH_METRES and mine & theirs)
        or (metres(point, p) <= FULL_NAME_MATCH_METRES and len(mine & theirs) >= 2)
        for p, theirs in city
    )


def national_register(rows: dict[str, list], city: list) -> None:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USBND"]
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
            if (clean(attrs.get("City")) or "").lower() != "bend":
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                continue
            if "historic district" in name.lower():
                continue
            seen.add(ref)
            if carried(point_of(geometry), name, city):
                already += 1
                continue
            added += 1
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"bend-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["register_number"].append(ref)
            rows["year_built"].append(None)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    if not seen:
        raise RuntimeError("no National Register listings inside Bend's bounds")
    print(
        f"Bend NRHP: {len(seen)} listings in bounds, {already} already on the City "
        f"register, {added} added",
        file=sys.stderr,
    )


def build() -> pa.Table:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    city = bend_register(rows)
    national_register(rows, city)
    n = len(rows["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(rows["landmark_id"], type=pa.string()),
            "name": pa.array(rows["name"], type=pa.string()),
            "city": pa.array(["USBND"] * n, type=pa.string()),
            "geometry_raw": pa.array(rows["geometry_raw"], type=pa.string()),
            "address": pa.array(rows["address"], type=pa.string()),
            "lm_type": pa.array(rows["lm_type"], type=pa.string()),
            "register_number": pa.array(rows["register_number"], type=pa.string()),
            "year_built": pa.array(rows["year_built"], type=pa.int64()),
            "year_designated": pa.array(rows["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""San José, CA's City Landmarks and National Register listings.

Two registers, both read live from ArcGIS layers:

* **San José's City Landmarks**, from the city's Historic Resources Inventory
  ("Historical Resources Inventory", ArcGIS item c4eb3973a70d40b688fbe6971035761b,
  owner `sanjose`; listed on data.sanjoseca.gov as `historical-resources-inventory`
  under Creative Commons Attribution), served as
  `OPN/OPN_OpenDataService/MapServer/406` from geo.sanjoseca.gov -- the server
  that serves the trees.  The inventory holds 4,849 points on 2026-11-16, most
  of them surveyed buildings with no designation; its status flags say which
  are designated.  `CLS` = 1 is a City Landmark Structure (209 rows) and
  `LANDMARKID` is the city's landmark number (`HL-137`, 208 rows); 216 rows
  carry one or the other (a landmark number with `CLS` = 0 is a landmark
  whose flag was never set: Knapp Residence, The Corner House, HL-41).  Only
  those are read.  Of the 216:

  * 3 are marked `DEMOLISHED` (two lost to fire, and KNTV's broadcast building)
    and are not published: the building is gone.
  * 4 have no `RESOURCENAME`: recorded by address only.  A pin named after its
    street address is not a landmark (Eugene's rule), so they are not
    published.

  `LANDMARKID` is not unique -- the five Martin Avenue houses designated
  together share HL-220, and three numbers are each on two different
  buildings, data-entry slips -- so the key is `FACILITYID`, the inventory's
  own number (unique over the layer), as `sjc-hri-{FACILITYID}`.  The
  landmark number is `register_number` (`HL-137`).  `RESOLUTIONYEAR` (the
  council resolution) gives `year_designated`, falling back to the year of
  `CCAPPROVAL`; `TRUEYR` gives `year_built` (`0000` is the inventory's
  unknown), falling back to the first year in `ESTIMATEDYR` (`c1926`).
  The candidate landmarks (`CCL`), the district contributors (`CLD`) and the
  structures of merit are not designated landmarks and are not read; the
  historic districts (layer 408) are areas, not landmarks (Durham's rule).

* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) within
  `CITY_BOUNDS["USSJC"]` and with `City` = San Jose (or Alviso, part of the
  city since 1968; one listing spells it `San Joes`) -- the box also holds
  Santa Clara, Campbell, Los Gatos, Saratoga, Milpitas and Cupertino
  listings.  Listed buildings, sites and structures, keyed
  `sjc-nr-{NRIS_Refnum}` with `CertDate` as `year_designated`; districts are
  areas and are not published.  NPS data is a US federal work.

A building on both registers keeps both rows, as Columbus does, because the
two designations are separate facts; `lm_type` says which register each row
is from.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_geometry_to_wkt,
    esri_ms_to_date,
    iter_features,
)
from shared.ingest import CITY_BOUNDS, emit, make_point_wkt

INVENTORY = FeatureLayer(
    "https://geo.sanjoseca.gov/server/rest/services/OPN/OPN_OpenDataService/MapServer/406"
)
INVENTORY_FIELDS = (
    "OBJECTID,FACILITYID,LANDMARKID,RESOURCENAME,STREETNUMBER1,STREETNAME,"
    "TRUEYR,ESTIMATEDYR,RESOLUTIONYEAR,CCAPPROVAL,DEMOLISHED,CLS"
)
INVENTORY_WHERE = "CLS = 1 OR LANDMARKID IS NOT NULL"

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,City,CertDate,STATUS"
# NPS's `City` for listings inside San José: Alviso was annexed in 1968, and
# one listing carries the typo.
NRHP_CITIES = frozenset({"san jose", "san josé", "san joes", "alviso"})

LOCAL_TYPE = "City of San José Landmark"
NATIONAL_TYPE = "National Register of Historic Places"

COLUMNS = (
    "landmark_id",
    "name",
    "geometry_raw",
    "address",
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


def first_year(value) -> int | None:
    """`1898` -> 1898, `c1926` -> 1926, `1907-1912` -> 1907; `0000` -> None."""
    match = re.search(r"\d{4}", str(value or ""))
    if not match or match.group() == "0000":
        return None
    return int(match.group())


def format_address(number, street) -> str | None:
    """`312` + `Third Street, S.` -> `312 S. Third Street` (the inventory
    writes its street names inverted for sorting)."""
    street = clean(street)
    number = clean(number)
    if street and "," in street:
        name, _, prefix = street.rpartition(",")
        street = f"{prefix.strip()} {name.strip()}"
    parts = [p for p in (number, street) if p]
    return " ".join(parts) or None


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def city_landmarks() -> dict[str, list]:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    features = fetch_all(
        INVENTORY,
        out_fields=INVENTORY_FIELDS,
        where=INVENTORY_WHERE,
        return_geometry=True,
        out_sr=4326,
    )
    if not features:
        raise RuntimeError(f"{INVENTORY.base} returned no City Landmarks")
    seen: set[str] = set()
    demolished = unnamed = unplaced = 0
    for feature in sorted(features, key=lambda f: f["attributes"]["OBJECTID"]):
        attrs = feature.get("attributes") or {}
        if attrs.get("DEMOLISHED") == 1:
            demolished += 1
            continue
        name = clean(attrs.get("RESOURCENAME"))
        if not name:
            unnamed += 1
            continue
        geom = feature.get("geometry") or {}
        wkt = make_point_wkt(geom.get("x"), geom.get("y"))
        if wkt is None or "NaN" in wkt:
            unplaced += 1
            continue
        facility = clean(attrs.get("FACILITYID"))
        if not facility:
            raise RuntimeError(
                f"San José inventory OBJECTID {attrs.get('OBJECTID')} has no FACILITYID"
            )
        if facility in seen:
            raise RuntimeError(f"San José inventory FACILITYID {facility} repeats")
        seen.add(facility)
        landmark_number = attrs.get("LANDMARKID")
        approved = esri_ms_to_date(attrs.get("CCAPPROVAL"))
        designated = attrs.get("RESOLUTIONYEAR") or (approved.year if approved else None)
        rows["landmark_id"].append(f"sjc-hri-{facility}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(format_address(attrs.get("STREETNUMBER1"), attrs.get("STREETNAME")))
        rows["year_built"].append(
            first_year(attrs.get("TRUEYR")) or first_year(attrs.get("ESTIMATEDYR"))
        )
        rows["lm_type"].append(LOCAL_TYPE)
        rows["register_number"].append(
            f"HL-{int(landmark_number)}" if landmark_number is not None else None
        )
        rows["year_designated"].append(int(designated) if designated else None)
    print(
        f"San José City Landmarks: {len(features)} read, {len(rows['landmark_id'])} published, "
        f"{demolished} demolished, {unnamed} unnamed, {unplaced} without a point",
        file=sys.stderr,
    )
    return rows


def national_register() -> dict[str, list]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USSJC"]
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
            if (clean(attrs.get("City")) or "").lower() not in NRHP_CITIES:
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                continue
            seen.add(ref)
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"sjc-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["year_built"].append(None)
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["register_number"].append(ref)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    print(f"San José National Register: {len(rows['landmark_id'])} published", file=sys.stderr)
    return rows


def build() -> pa.Table:
    local = city_landmarks()
    national = national_register()
    data = {k: local[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USSJC"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "year_built": pa.array(data["year_built"], type=pa.int64()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Cockburn's heritage places: the City of Cockburn's Local Heritage Survey
(formerly the Local Government Inventory), from the city's ArcGIS Online.

No licence stated; displayed with attribution only, not licensed for reuse.

Source: "Heritage Map Public_WFL1" (item 19b51e4ff02a4c32ad0c92d7f08e005a,
owned by the City's GIS team on the same organisation that publishes the
street trees; "Updated with FME"), layer 2 "Heritage - Local Govt Inventory",
the layer behind the Mapping Hub's Heritage theme.  The item states no
licence.  It is the City's own register of heritage places: each records its
management category (A Exceptional .. D Some significance, T Significant
Tree(s)), whether it is on the City's statutory Heritage List, and whether it
is also on the State Register of Heritage Places.

The State Register itself (Heritage Council of WA, DPLH-006 on data.wa.gov.au)
was not used: SLIP serves it only behind a login under a "Custom (Active
Acceptance)" licence (Perth, AUPER, hit the same), and the 18 State-registered
places in the City are flagged on this layer.

Measured 2026-11-21 (layer `dataLastEditDate` 2026-11-20): 136 points over
120 inventory numbers (`LGI_`), every point inside the City.  A place mapped
at several points (the Woodman Point tuarts, the Roe Highway tree corridor)
is one landmark with a MULTIPOINT.  19 places with a `Demolished_Date` (the
Dutch Windmill, 2012; "Tuart Tree, Henderson (site)", removed 2007; ...) are
kept in the inventory as a record and are not published, leaving 101
(14 State Register, 31 Heritage List, 56 survey only).

Field mapping:

  LGI_                -> landmark_id ("ckb-lhs-{LGI_}") and register_number
  Name                -> name (the inventory number is appended when two
                         places share a name)
  State_Register,
  Heritage_List       -> protection_type: "State Register", "City of Cockburn
                         Heritage List" or "Local Heritage Survey"
  Management_Category -> category
  Construction_Date   -> year_built: the first four-digit year in the free
                         text ("c.1920", "1905-1920s"); null when there is none
  point geometry      -> geometry_raw (POINT or MULTIPOINT WKT)
"""

import re
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import emit

# Kept in step with cockburn_landmarks_probe.py.
LAYER = FeatureLayer(
    "https://services-ap1.arcgis.com/pvAgeoXPDsVDgOQ0/arcgis/rest/services/"
    "Heritage_Map_Public_WFL1/FeatureServer/2"
)

OUT_FIELDS = (
    "OBJECTID,LGI_,Name,Management_Category,Heritage_List,State_Register,"
    "Construction_Date,Demolished_Date"
)

_YEAR = re.compile(r"\b(1[6-9]\d\d|20[0-2]\d)\b")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def is_yes(value) -> bool:
    return (clean(value) or "").lower() == "yes"


def protection_of(attrs: dict) -> str:
    if is_yes(attrs.get("State_Register")):
        return "State Register"
    if is_yes(attrs.get("Heritage_List")):
        return "City of Cockburn Heritage List"
    return "Local Heritage Survey"


def year_of(value) -> int | None:
    match = _YEAR.search(clean(value) or "")
    return int(match.group(1)) if match else None


def transform(features: list[dict]) -> pa.Table:
    places: dict[str, dict] = {}
    for feature in features:
        attrs = feature.get("attributes") or {}
        number = clean(attrs.get("LGI_"))
        lat, lon = esri_point(feature.get("geometry"))
        if number is None or lat is None or lon is None:
            continue
        place = places.setdefault(number, {"attrs": attrs, "points": []})
        place["points"].append((lon, lat))

    current = {
        number: place
        for number, place in places.items()
        if clean(place["attrs"].get("Demolished_Date")) is None
        and clean(place["attrs"].get("Name")) is not None
    }
    if not current:
        raise RuntimeError("City of Cockburn heritage inventory returned no current places")
    gone = len(places) - len(current)

    repeats = Counter(clean(p["attrs"]["Name"]) for p in current.values())
    rows: dict[str, list] = {
        k: [] for k in (
            "landmark_id", "name", "geometry_raw", "protection_type",
            "register_number", "category", "year_built",
        )
    }
    for number, place in sorted(current.items()):
        attrs = place["attrs"]
        name = clean(attrs["Name"])
        if repeats[name] > 1:
            name = f"{name} ({number})"
        points = sorted(set(place["points"]))
        if len(points) == 1:
            wkt = f"POINT({points[0][0]} {points[0][1]})"
        else:
            wkt = "MULTIPOINT(" + ", ".join(f"({x} {y})" for x, y in points) + ")"
        rows["landmark_id"].append(f"ckb-lhs-{number}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["protection_type"].append(protection_of(attrs))
        rows["register_number"].append(number)
        rows["category"].append(clean(attrs.get("Management_Category")))
        rows["year_built"].append(year_of(attrs.get("Construction_Date")))

    print(
        f"Cockburn landmarks: {len(current)} places from {len(features)} points "
        f"({gone} demolished places left out)",
        file=sys.stderr,
    )
    n = len(rows["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(rows["landmark_id"], type=pa.string()),
            "city": pa.array(["AUCKB"] * n, type=pa.string()),
            "name": pa.array(rows["name"], type=pa.string()),
            "geometry_raw": pa.array(rows["geometry_raw"], type=pa.string()),
            "protection_type": pa.array(rows["protection_type"], type=pa.string()),
            "register_number": pa.array(rows["register_number"], type=pa.string()),
            "category": pa.array(rows["category"], type=pa.string()),
            "year_built": pa.array(rows["year_built"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    features: list[dict] = []
    for page in iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, order_by="OBJECTID"
    ):
        features.extend(page)
    emit(transform(features))

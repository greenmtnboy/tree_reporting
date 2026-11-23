#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Whitby's (Ontario) parks and Town facilities, from the Town's open data.

**No heritage register is published as data.**  The Town's Municipal Heritage
Register (designated and listed properties) is a PDF of addresses on
whitby.ca, with no licence and no coordinates; nothing on the Town's ArcGIS
Online organisation or its open data site (Whitby GeoHub,
geohub-whitby.hub.arcgis.com, 20 datasets on 2026-11-23) is a heritage layer,
and the "Landmark Facilities" view on the same organisation is six libraries
and attractions released "for your personal, non-commercial use".  So, as
Fort St. John did, this takes the Town's own registers of named places from
the open data site, both under The Corporation of the Town of Whitby Open
Government License v1.0
(https://whitby.maps.arcgis.com/sharing/rest/content/items/223810efc31c40b3aff99dd74f809a97/data):

  * **"Parks and Open Spaces"** (item 6fa989ed24c64eb7bdcf323fb25e7d28,
    `WhitbyParks/FeatureServer/0`), 529 polygons classed by `CLASS`.  The
    parks are kept -- `TOWN PARK`, `DISTRICT`, `LOCAL`, `PARKETTE`,
    `WATERFRONT (TOWN)`, `CEMETERY`, `OFFLEASH`, `FEATURE DISPLAY`,
    `FACILITY` -- and the 330 `OPENSPACE` strips (17 pieces of "Hydro
    Corridor Open Space"), 47 storm-water ponds and leased lands are not.
    A park drawn in several polygons shares one `FACILITYID` (`PD66-01` x3)
    and is published as one multipolygon.  Id `whitby-park-{FACILITYID}`.
  * **"Town Facilities"** (item 686a4bbbc7ae4d9aa8555caa07cc71b1,
    `WhitbyTownFacilities/FeatureServer/0`), 27 points: community and
    recreation centres, arenas, fire halls, the museum and gallery, the
    marina, the municipal building.  A facility whose name is a park's name
    (Groveside Cemetery) is that park and is skipped; the layer lists one
    building twice under two types (Brooklin Community Centre and Library),
    and the first is kept.  The layer has no id but `OBJECTID`, so the id is
    `whitby-fac-{OBJECTID}`; a reload re-keys them, which costs only churn
    since landmarks are rebuilt whole.

`property_type` is the park's class (title-cased) or the facility's `TYPE`.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import emit

BASE = "https://services5.arcgis.com/ATdLnvuMRJk8AGkQ/arcgis/rest/services"
PARKS = FeatureLayer(f"{BASE}/WhitbyParks/FeatureServer/0", timeout=120)
FACILITIES = FeatureLayer(f"{BASE}/WhitbyTownFacilities/FeatureServer/0", timeout=120)

PARK_CLASSES = (
    "TOWN PARK",
    "DISTRICT",
    "LOCAL",
    "PARKETTE",
    "WATERFRONT (TOWN)",
    "CEMETERY",
    "OFFLEASH",
    "FEATURE DISPLAY",
    "FACILITY",
)
PARK_WHERE = "CLASS IN (" + ", ".join(f"'{c}'" for c in PARK_CLASSES) + ")"
CLASS_LABEL = {"WATERFRONT (TOWN)": "Waterfront Park", "OFFLEASH": "Off-Leash Park"}


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("’", "'").split())
    return text or None


def key_of(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def fetch(layer: FeatureLayer, out_fields: str, where: str = "1=1") -> list[dict]:
    rows: list[dict] = []
    for page in iter_features(
        layer,
        out_fields=out_fields,
        where=where,
        return_geometry=True,
        out_sr=4326,
        order_by="OBJECTID",
    ):
        rows.extend(page)
    return rows


def build() -> pa.Table:
    landmark_id: list[str] = []
    name: list[str] = []
    geometry_raw: list[str] = []
    address: list[str | None] = []
    property_type: list[str | None] = []

    # One park, several polygons: gather every part's rings under its id.
    parks: dict[str, dict] = {}
    for feature in fetch(PARKS, "OBJECTID,FACILITYID,PARK_NAME,ADDRESS,CLASS", PARK_WHERE):
        rec = feature.get("attributes") or {}
        fid, label = clean(rec.get("FACILITYID")), clean(rec.get("PARK_NAME"))
        rings = (feature.get("geometry") or {}).get("rings")
        if fid is None or label is None or not rings or label.lower().startswith("unnamed"):
            continue
        park = parks.setdefault(
            fid,
            {"name": label, "address": clean(rec.get("ADDRESS")), "class": clean(rec.get("CLASS")), "rings": []},
        )
        park["rings"].extend(rings)

    park_names: set[str] = set()
    for fid, park in parks.items():
        wkt = esri_geometry_to_wkt({"rings": park["rings"]})
        if wkt is None:
            continue
        park_names.add(key_of(park["name"]))
        cls = park["class"]
        landmark_id.append(f"whitby-park-{fid}")
        name.append(park["name"])
        geometry_raw.append(wkt)
        address.append(park["address"])
        property_type.append(CLASS_LABEL.get(cls, cls.title()) if cls else None)
    n_parks = len(landmark_id)

    seen_facilities: set[str] = set()
    for feature in fetch(FACILITIES, "OBJECTID,NAME,ADDRESS,TYPE"):
        rec = feature.get("attributes") or {}
        oid, label = rec.get("OBJECTID"), clean(rec.get("NAME"))
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if oid is None or label is None or wkt is None:
            continue
        key = key_of(label)
        if key in park_names or key in seen_facilities:
            continue
        seen_facilities.add(key)
        landmark_id.append(f"whitby-fac-{oid}")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(clean(rec.get("ADDRESS")))
        property_type.append(clean(rec.get("TYPE")))

    n = len(landmark_id)
    if len(set(landmark_id)) != n:
        raise RuntimeError("Whitby landmarks: repeated landmark_id")
    if n_parks == 0:
        raise RuntimeError("Whitby parks layer produced no parks")
    print(
        f"Whitby landmarks: {n} place(s) -- {n_parks} park(s), {n - n_parks} facility(ies)",
        file=sys.stderr,
    )
    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(["CAWHI"] * n, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "property_type": pa.array(property_type, type=pa.string()),
        }
    )


if __name__ == "__main__":
    emit(build())

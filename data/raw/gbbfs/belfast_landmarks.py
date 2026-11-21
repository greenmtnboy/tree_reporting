#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Belfast's listed buildings and scheduled monuments, from the Department
for Communities' Historic Environment Division (HED).

Source: HED's public FeatureServer, "Historic_Environment_Division_GIS_Data"
(listed on OpenDataNI as "Historic Environment Division ESRI REST API",
licence "UK Open Government Licence (OGL)"; "updated on a monthly basis"):

* layer 1, the Historic Buildings Record, cut to `Council = 'Belfast'` and to
  the listed grades (A, B+, B1, B2 and the older B).  `Record Only` and
  `D1 Record Only` rows are unlisted, delisted or demolished buildings and
  are not landmarks.  `HB_ref` (`HB26/28/061`) is the listing reference,
  unique over the kept rows; the name is the first line of `Address`, which
  is a building name where one exists and the street address otherwise.
* layer 0, the Sites and Monuments Record, cut to `Council = 'Belfast'` and
  to the protected sites (`Scheduled`, `State Care`, `State Care and
  Scheduled`).  `SMRNo` (`ANT056:018`) is the reference; the name is the
  monument type and townland (`Promontory fort: McArt's fort (Ballyaghagan)`).

The council filter is HED's own attribution to the post-2015 Belfast council
area, not a box, so the landmarks are the city's.  `lm_type` names the
designation and grade, `register_number` the HED reference.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, iter_features
from shared.ingest import emit, make_point_wkt

SERVICE = (
    "https://services2.arcgis.com/BdBkthNLO9mzGAMO/arcgis/rest/services/"
    "Historic_Environment_Division_GIS_Data/FeatureServer"
)
BUILDINGS = FeatureLayer(f"{SERVICE}/1")
MONUMENTS = FeatureLayer(f"{SERVICE}/0")

COUNCIL = "Belfast"
LISTED_GRADES = ("A", "B+", "B1", "B2", "B")
PROTECTED = ("Scheduled", "State Care", "State Care and Scheduled")

COLUMNS = ("landmark_id", "name", "geometry_raw", "address", "lm_type", "register_number")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _in(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def fetch_all(layer: FeatureLayer, where: str, out_fields: str) -> list[dict]:
    return [
        feature
        for page in iter_features(
            layer, where=where, out_fields=out_fields, return_geometry=True, out_sr=4326
        )
        for feature in page
    ]


def listed_buildings(rows: dict[str, list]) -> int:
    features = fetch_all(
        BUILDINGS,
        f"Council = '{COUNCIL}' AND CurrentGra IN ({_in(LISTED_GRADES)})",
        "OBJECTID,HB_ref,CurrentGra,Address",
    )
    if not features:
        raise RuntimeError(f"{BUILDINGS.base} returned no listed buildings in Belfast")
    seen: set[str] = set()
    kept = 0
    for feature in features:
        attrs = feature.get("attributes") or {}
        geom = feature.get("geometry") or {}
        ref = clean(attrs.get("HB_ref"))
        lines = [clean(line) for line in str(attrs.get("Address") or "").splitlines()]
        lines = [line for line in lines if line]
        wkt = make_point_wkt(geom.get("x"), geom.get("y"))
        if not ref or not lines or wkt is None:
            continue
        if ref in seen:
            raise RuntimeError(f"Belfast listed building {ref} is repeated")
        seen.add(ref)
        rows["landmark_id"].append(f"belfast-hb-{ref}")
        rows["name"].append(lines[0])
        rows["geometry_raw"].append(wkt)
        rows["address"].append(", ".join(lines))
        rows["lm_type"].append(f"Listed building, Grade {clean(attrs.get('CurrentGra'))}")
        rows["register_number"].append(ref)
        kept += 1
    return kept


def scheduled_monuments(rows: dict[str, list]) -> int:
    features = fetch_all(
        MONUMENTS,
        f"Council = '{COUNCIL}' AND Protection IN ({_in(PROTECTED)})",
        "OBJECTID,SMRNo,Townland_s_,Edited_Type,Protection",
    )
    seen: set[str] = set()
    kept = 0
    for feature in features:
        attrs = feature.get("attributes") or {}
        geom = feature.get("geometry") or {}
        ref = clean(attrs.get("SMRNo"))
        kind = clean(attrs.get("Edited_Type"))
        townland = clean(attrs.get("Townland_s_"))
        wkt = make_point_wkt(geom.get("x"), geom.get("y"))
        if not ref or not kind or wkt is None:
            continue
        if ref in seen:
            raise RuntimeError(f"Belfast monument {ref} is repeated")
        seen.add(ref)
        name = kind.capitalize()
        if townland:
            name = f"{name} ({townland.title()})"
        rows["landmark_id"].append(f"belfast-smr-{ref}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(None)
        rows["lm_type"].append(f"Monument ({clean(attrs.get('Protection'))})")
        rows["register_number"].append(ref)
        kept += 1
    return kept


def build() -> pa.Table:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    buildings = listed_buildings(rows)
    monuments = scheduled_monuments(rows)
    print(
        f"Belfast landmarks: {buildings} listed buildings, {monuments} protected monuments",
        file=sys.stderr,
    )
    n = len(rows["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(rows["landmark_id"], type=pa.string()),
            "name": pa.array(rows["name"], type=pa.string()),
            "city": pa.array(["GBBFS"] * n, type=pa.string()),
            "geometry_raw": pa.array(rows["geometry_raw"], type=pa.string()),
            "address": pa.array(rows["address"], type=pa.string()),
            "lm_type": pa.array(rows["lm_type"], type=pa.string()),
            "register_number": pa.array(rows["register_number"], type=pa.string()),
        }
    )


if __name__ == "__main__":
    emit(build())

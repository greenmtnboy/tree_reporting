#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Wellington, NZ's scheduled historic heritage (2024 District Plan).

Source: the Wellington City 2024 District Plan's heritage schedules, layers
of `2024DistrictPlan/2024DistrictPlan/MapServer` on Wellington City
Council's ArcGIS Server, gis.wcc.govt.nz -- the same server as the trees:

* 67 `Heritage Buildings` (SCHED1, points; 634 rows for 573 items on
  2026-11-17),
* 70 `Heritage Structures` (SCHED2, points; 24),
* 73 `Heritage Areas` (SCHED3, polygons; 43).

Licence: the council's open data hub states "Wellington City Council GIS
data that can be accessed online is made available under the Creative
Commons Attribution 4.0 International (CC BY 4.0) License"
(https://data-wcc.opendata.arcgis.com/).  The hub items' own licence field
says only that the layer "has been created for the District Plan of
Wellington City Council. If you require something beyond this purpose,
please contact the WCC District Planning team" -- a purpose note, not a
licence or a prohibition.

The schedules' Notable Trees (SCHED6, layer 78) are a notable-tree
register, not a landmark list, and are not read here.

* `landmark_id` is `wlg-{b|s|a}{DPRef}`: the schedule letter (Buildings,
  Structures, Areas) and the plan's own reference.  One SCHED1 reference can
  cover several buildings, each its own point (`407`, Four Houses, 206-212
  The Esplanade); they are one scheduled item and are published as one
  landmark with a MULTIPOINT.
* `register_number` is the plan reference, `lm_type` the plan's type
  (`Heritage Building`, `Heritage structure`, `Heritage Area`), `address`
  the full address.
* Every row is `Status = Operative` today; a row with another status is not
  published.
"""

import sys
from collections import defaultdict
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import emit

BASE = "https://gis.wcc.govt.nz/arcgis/rest/services/2024DistrictPlan/2024DistrictPlan/MapServer"
# (layer id, landmark_id letter)
LAYERS = ((67, "b"), (70, "s"), (73, "a"))
FIELDS = "OBJECTID,DPRef,Name,FullAddress,Type,Status"

COLUMNS = ("landmark_id", "name", "geometry_raw", "lm_type", "register_number", "address")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def merge_points(wkts: list[str]) -> str:
    """One MULTIPOINT of several `POINT(x y)` strings, or the polygon as is."""
    if len(wkts) == 1:
        return wkts[0]
    inner = []
    for wkt in wkts:
        if not wkt.upper().startswith("POINT"):
            raise RuntimeError(f"Wellington landmarks: cannot merge {wkt[:30]!r}")
        inner.append("(" + wkt[wkt.index("(") + 1: wkt.rindex(")")].strip() + ")")
    return "MULTIPOINT(" + ", ".join(inner) + ")"


def build() -> pa.Table:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    skipped = 0
    for layer_id, letter in LAYERS:
        layer = FeatureLayer(f"{BASE}/{layer_id}", timeout=180)
        features = [
            f
            for page in iter_features(
                layer, out_fields=FIELDS, return_geometry=True, out_sr=4326, order_by="OBJECTID"
            )
            for f in page
        ]
        if not features:
            raise RuntimeError(f"{layer.base} returned no heritage items")
        groups: dict[str, list[dict]] = defaultdict(list)
        for feature in features:
            attrs = feature.get("attributes") or {}
            ref = clean(attrs.get("DPRef"))
            if clean(attrs.get("Status")) != "Operative" or ref is None or not clean(attrs.get("Name")):
                skipped += 1
                continue
            if esri_geometry_to_wkt(feature.get("geometry")) is None:
                skipped += 1
                continue
            groups[ref].append(feature)
        for ref, members in groups.items():
            first = members[0]["attributes"]
            rows["landmark_id"].append(f"wlg-{letter}{ref}")
            rows["name"].append(clean(first.get("Name")))
            rows["geometry_raw"].append(
                merge_points([esri_geometry_to_wkt(m["geometry"]) for m in members])
            )
            rows["lm_type"].append(clean(first.get("Type")))
            rows["register_number"].append(ref)
            rows["address"].append(clean(first.get("FullAddress")))

    if len(set(rows["landmark_id"])) != len(rows["landmark_id"]):
        raise RuntimeError("Wellington heritage items: repeated landmark_id")
    if skipped:
        print(f"Wellington landmarks: skipped {skipped} rows", file=sys.stderr)
    n = len(rows["landmark_id"])
    print(f"Wellington landmarks: {n} scheduled heritage items", file=sys.stderr)
    return pa.table(
        {
            "landmark_id": pa.array(rows["landmark_id"], type=pa.string()),
            "name": pa.array(rows["name"], type=pa.string()),
            "city": pa.array(["NZWLG"] * n, type=pa.string()),
            "geometry_raw": pa.array(rows["geometry_raw"], type=pa.string()),
            "lm_type": pa.array(rows["lm_type"], type=pa.string()),
            "register_number": pa.array(rows["register_number"], type=pa.string()),
            "address": pa.array(rows["address"], type=pa.string()),
        }
    )


if __name__ == "__main__":
    emit(build())

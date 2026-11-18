#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Sydney's heritage places: the NSW State Heritage Register and the Sydney LEP 2012 heritage schedule.

Two statutory registers, both read live from the NSW Government's ArcGIS
server, both for the City of Sydney LGA only:

* **NSW State Heritage Register** ("State Heritage Register - Curtilages",
  NSW Department of Climate Change, Energy, the Environment and Water,
  `EDP/SHR_Curtilage/MapServer/0`; data.nsw.gov.au dataset
  `state-heritage-register-curtilages1c5ee`, "Creative Commons Attribution").
  The listed curtilage polygon of every place of State significance, `LGA =
  'SYDNEY'`: 424 places on 2026-11-18 (the Queen Victoria Building, the Town
  Hall, Elizabeth Bay House, ...).  Keyed `syd-shr-{HOITEMID}` (the heritage
  database's item id, unique on the layer), with the SHR listing number as
  `register_number`, the listing year as `year_designated`, the register's
  `TYPE` (Built, Landscape, Complex / Group, ...) as `category` and its
  `ADDRESS`.
* **Sydney Local Environmental Plan 2012, Schedule 5 heritage items**
  ("Environmental Planning Instrument - Heritage (HER)", NSW Department of
  Planning, Housing and Infrastructure, `Planning/EPI_Primary_Planning_Layers/
  MapServer/0`; data.nsw.gov.au dataset
  `environmental-planning-instrument-heritage-her`, "Creative Commons
  Attribution").  The items of *local* significance (`SIG = 'Local'`) of the
  Sydney LEP 2012 and the small Harold Park LEP 2011: 2,067 polygons on
  2026-11-18.  Items of State significance are left to the SHR above, which
  lists the same places with their own curtilage; heritage conservation areas
  (whole streets and suburbs) are not landmarks and are not read.  An item's
  polygons are merged into one by its schedule number `H_ID` (36 items have
  two); a few carry two names across their parts, and the commoner, then the
  longer, is used.  Keyed `syd-lep-{H_ID}` (Harold Park `syd-hp-{H_ID}`, since
  its I1-I3 repeat the LEP 2012 numbers), with `H_ID` as `register_number`.

Places on State land under a SEPP heritage map (Barangaroo, Redfern-Eveleigh)
are in neither schedule and are covered only where the SHR lists them.
"""

import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_geometry_to_wkt,
    esri_ms_to_date,
    iter_features,
)
from shared.ingest import emit

SHR = FeatureLayer(
    "https://mapprod3.environment.nsw.gov.au/arcgis/rest/services/EDP/SHR_Curtilage/MapServer/0",
    timeout=180,
)
SHR_FIELDS = "OBJECTID,HOITEMID,ITEMNAME,ADDRESS,LISTING,LISTINGNO,LISTING_DATE,TYPE"
SHR_WHERE = "LGA = 'SYDNEY' AND LISTING = 'SHR'"
SHR_TYPE = "NSW State Heritage Register"

EPI = FeatureLayer(
    "https://mapprod3.environment.nsw.gov.au/arcgis/rest/services/Planning/"
    "EPI_Primary_Planning_Layers/MapServer/0",
    timeout=180,
)
EPI_FIELDS = "OBJECTID,EPI_NAME,LAY_CLASS,H_ID,H_NAME,SIG"
# The plan each item comes from: (id prefix, lm_type).
PLANS = {
    "Sydney Local Environmental Plan 2012": ("syd-lep-", "Sydney LEP 2012 heritage item"),
    "Sydney Local Environmental Plan (Harold Park) 2011": (
        "syd-hp-", "Sydney LEP (Harold Park) 2011 heritage item",
    ),
}
EPI_WHERE = (
    "EPI_NAME IN ({}) AND LAY_CLASS LIKE 'Item%' AND SIG = 'Local'".format(
        ", ".join(f"'{name}'" for name in PLANS)
    )
)

COLUMNS = (
    "landmark_id", "name", "geometry_raw", "address", "category",
    "lm_type", "register_number", "year_designated",
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def state_register(rows: dict[str, list]) -> None:
    seen: set[int] = set()
    for page in iter_features(
        SHR, out_fields=SHR_FIELDS, where=SHR_WHERE, return_geometry=True, out_sr=4326,
    ):
        for feature in page:
            attrs = feature.get("attributes") or {}
            item_id = attrs.get("HOITEMID")
            name = clean(attrs.get("ITEMNAME"))
            wkt = esri_geometry_to_wkt(feature.get("geometry"))
            if item_id is None or name is None or wkt is None:
                continue
            if int(item_id) in seen:
                raise RuntimeError(f"SHR item {item_id} appears twice in {SHR.base}")
            seen.add(int(item_id))
            listed = esri_ms_to_date(attrs.get("LISTING_DATE"))
            rows["landmark_id"].append(f"syd-shr-{int(item_id)}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("ADDRESS")))
            rows["category"].append(clean(attrs.get("TYPE")))
            rows["lm_type"].append(SHR_TYPE)
            rows["register_number"].append(clean(attrs.get("LISTINGNO")))
            rows["year_designated"].append(listed.year if listed else None)
    if not seen:
        raise RuntimeError(f"no State Heritage Register places read for Sydney from {SHR.base}")
    print(f"Sydney landmarks: {len(seen)} State Heritage Register places", file=sys.stderr)


def local_items(rows: dict[str, list]) -> None:
    items: dict[tuple[str, str], dict] = {}
    for page in iter_features(
        EPI, out_fields=EPI_FIELDS, where=EPI_WHERE, return_geometry=True, out_sr=4326,
    ):
        for feature in page:
            attrs = feature.get("attributes") or {}
            plan = clean(attrs.get("EPI_NAME"))
            h_id = clean(attrs.get("H_ID"))
            name = clean(attrs.get("H_NAME"))
            rings = (feature.get("geometry") or {}).get("rings") or []
            if plan not in PLANS or h_id is None or name is None or not rings:
                continue
            item = items.setdefault((plan, h_id), {"rings": [], "names": Counter()})
            item["rings"].extend(rings)
            item["names"][name] += 1
    if not items:
        raise RuntimeError(f"no Sydney LEP heritage items read from {EPI.base}")
    for (plan, h_id), item in sorted(items.items()):
        wkt = esri_geometry_to_wkt({"rings": item["rings"]})
        if wkt is None:
            continue
        prefix, lm_type = PLANS[plan]
        name = max(item["names"], key=lambda n: (item["names"][n], len(n), n))
        rows["landmark_id"].append(f"{prefix}{h_id}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(None)
        rows["category"].append(None)
        rows["lm_type"].append(lm_type)
        rows["register_number"].append(h_id)
        rows["year_designated"].append(None)
    print(f"Sydney landmarks: {len(items)} local LEP heritage items", file=sys.stderr)


def build() -> pa.Table:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    state_register(rows)
    local_items(rows)
    n = len(rows["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(rows["landmark_id"], type=pa.string()),
            "city": pa.array(["AUSYD"] * n, type=pa.string()),
            "name": pa.array(rows["name"], type=pa.string()),
            "geometry_raw": pa.array(rows["geometry_raw"], type=pa.string()),
            "address": pa.array(rows["address"], type=pa.string()),
            "category": pa.array(rows["category"], type=pa.string()),
            "lm_type": pa.array(rows["lm_type"], type=pa.string()),
            "register_number": pa.array(rows["register_number"], type=pa.string()),
            "year_designated": pa.array(rows["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())

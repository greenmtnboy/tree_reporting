#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Christchurch, NZ's scheduled heritage items (Christchurch District Plan).

Source: "DP Heritage Item (OpenData)" (ArcGIS item
f756c115142041fea96c3ac778ecbde4, owner `CCCSpatial`),
`OpenData/DistrictPlanB/FeatureServer/31` on Christchurch City Council's
ArcGIS Server, the same server as the trees; licence per the item: "CCC -
Open Data - Use of this dataset is permitted in accordance with the Creative
Commons Attribution 4.0 International (CC BY 4.0) License".  It is the
District Plan's Schedule of Significant Historic Heritage (Appendix 9.3.7.2):
the places the plan protects, each a polygon (the item and its setting
mapped as one area).  709 rows on 2026-11-16, every one `LegalStatus =
Operative` with no `RemovedByPlanChangeIdentifier`; a row that gains either
is not published.

* `landmark_id` is `chc-{ScheduleReference}`, the plan's own schedule
  number.  One number covers two polygons (647: Riccarton Grounds and
  Riccarton Bush); a number on more than one row is suffixed with the row's
  OID (`chc-647-624`), and only those.
* `register_number` is the schedule number.
* `lm_type` is the Heritage New Zealand List category the council records
  (`Category 1`, `Category 2`, `Historic Area`, or `Not Listed` -- protected
  by the plan alone); `category` the plan's own grading (`Highly
  Significant` / `Significant`); `neighborhood` the suburb (`Locality`).

`DPHeritageSetting` (layer 32) is the settings again, and the
`DPTreeProtection` layer (54) is the plan's scheduled trees -- a notable
register, not a landmark list; neither is read here.
"""

import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import emit

LAYER = FeatureLayer(
    "https://gis.ccc.govt.nz/server/rest/services/OpenData/DistrictPlanB/FeatureServer/31",
    timeout=180,
)
FIELDS = (
    "DPHeritageItemID,ScheduleReference,HeritageItemName,LegalStatus,"
    "RemovedByPlanChangeIdentifier,Significance,HeritageRegisterCategory,Locality"
)

COLUMNS = (
    "landmark_id",
    "name",
    "geometry_raw",
    "lm_type",
    "register_number",
    "category",
    "neighborhood",
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def build() -> pa.Table:
    features = [
        f
        for page in iter_features(
            LAYER,
            out_fields=FIELDS,
            return_geometry=True,
            out_sr=4326,
            order_by="DPHeritageItemID",
        )
        for f in page
    ]
    if not features:
        raise RuntimeError(f"{LAYER.base} returned no heritage items")

    live = []
    skipped = 0
    for feature in features:
        attrs = feature.get("attributes") or {}
        if clean(attrs.get("LegalStatus")) != "Operative" or clean(
            attrs.get("RemovedByPlanChangeIdentifier")
        ):
            skipped += 1
            continue
        if attrs.get("ScheduleReference") is None or not clean(attrs.get("HeritageItemName")):
            skipped += 1
            continue
        live.append(feature)
    repeats = Counter(int(f["attributes"]["ScheduleReference"]) for f in live)

    rows: dict[str, list] = {k: [] for k in COLUMNS}
    for feature in live:
        attrs = feature["attributes"]
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if wkt is None:
            skipped += 1
            continue
        ref = int(attrs["ScheduleReference"])
        lid = f"chc-{ref}"
        if repeats[ref] > 1:
            lid = f"{lid}-{int(attrs['DPHeritageItemID'])}"
        rows["landmark_id"].append(lid)
        rows["name"].append(clean(attrs.get("HeritageItemName")))
        rows["geometry_raw"].append(wkt)
        rows["lm_type"].append(clean(attrs.get("HeritageRegisterCategory")))
        rows["register_number"].append(str(ref))
        rows["category"].append(clean(attrs.get("Significance")))
        rows["neighborhood"].append(clean(attrs.get("Locality")))

    if len(set(rows["landmark_id"])) != len(rows["landmark_id"]):
        raise RuntimeError("Christchurch heritage items: repeated landmark_id")
    if skipped:
        print(f"Christchurch landmarks: skipped {skipped} rows", file=sys.stderr)

    n = len(rows["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(rows["landmark_id"], type=pa.string()),
            "name": pa.array(rows["name"], type=pa.string()),
            "city": pa.array(["NZCHC"] * n, type=pa.string()),
            "geometry_raw": pa.array(rows["geometry_raw"], type=pa.string()),
            "lm_type": pa.array(rows["lm_type"], type=pa.string()),
            "register_number": pa.array(rows["register_number"], type=pa.string()),
            "category": pa.array(rows["category"], type=pa.string()),
            "neighborhood": pa.array(rows["neighborhood"], type=pa.string()),
        }
    )


if __name__ == "__main__":
    emit(build())

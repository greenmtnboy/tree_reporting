#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Hastings District, NZ's District Plan heritage items.

LICENCE: no licence stated; displayed with attribution only, not licensed
for reuse.  Source: "HeritageFeatures_DistrictPlan" (ArcGIS item
b48a779c6f0b4d15a4d50c345d72e964, owner `michaelww_HDCGIS`, Hastings
District Council's ArcGIS Online org -- the same org as the trees),
`HeritageFeatures_DistrictPlan/FeatureServer/0`: "Hastings District
Heritage Features - Acknowledgements - Heritage New Zealand (Pouhere
Taonga) and Hawkes Bay Knowledgebank", the layer behind the council's
"Heritage Buildings/Features within the Hastings District" story map.  Its
licence field is empty and it is not in the council's open data hub, whose
Creative Commons statement covers the hub's datasets.  98 points on
2026-11-21.  The District Plan's own heritage layer (`OperativeDP_EPlan` on
gismaps.hdc.govt.nz) did not answer from outside New Zealand.

Each row is a heritage item scheduled in the Hastings District Plan; its
structured facts are written into the HTML `Description` ("DISTRICT PLAN
REFERENCE NUMBER - HB76", "DISTRICT PLAN HERITAGE CATEGORY - I",
"ADDRESS - ..."), which is parsed here:

* `landmark_id` is `has-{ref}` from the plan reference (`has-hb76`).  One
  reference covers two points (HB16, Martin House and its kilns); a
  reference on more than one row is suffixed with the name
  (`has-hb16-martin-kilns`), and only those.  The four rows with no
  reference -- the three historic areas and the Green Hill Road oaks -- are
  `has-{name}`.
* `register_number` is the plan reference, `lm_type` the plan's heritage
  category (`Category I`, `Category II`, `Historic`, or `Historic Area`
  for the areas), `address` the address the description gives.
* Two items are pinned out in Hawke Bay, 20 km off the coast (Arden House
  and Tauroa Homestead, both in Havelock North, in November 2026); a point
  in the bay is not published, with a logged count.
"""

import html
import re
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, esri_point, iter_features
from shared.ingest import emit

LAYER = FeatureLayer(
    "https://services1.arcgis.com/8L3DQUzjrkgEmDpQ/arcgis/rest/services/"
    "HeritageFeatures_DistrictPlan/FeatureServer/0",
    timeout=180,
)
FIELDS = "OBJECTID,name,Description"

# Hawke Bay between Tangoio and Napier: open sea, no item can stand there.
BAY_LAT = (-39.55, -39.30)
BAY_MIN_LON = 176.98

REF_RE = re.compile(r"DISTRICT PLAN REFERENCE NUMBER\s*-?\s*([A-Z]{1,3})\s?(\d+)")
CATEGORY_RE = re.compile(r"DISTRICT PLAN HERITAGE CATEGORY\s*-?\s*([A-Za-z]+)")
ADDRESS_RE = re.compile(r"ADDRESS\s*-\s*(.*?)\s+LEGAL DESCRIPTION")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("�", " ").split())
    return text or None


def description_text(value) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return " ".join(text.replace("\xa0", " ").split())


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def in_bay(lat: float, lon: float) -> bool:
    return BAY_LAT[0] < lat < BAY_LAT[1] and lon > BAY_MIN_LON


def build() -> pa.Table:
    features = [
        f
        for page in iter_features(
            LAYER, out_fields=FIELDS, return_geometry=True, out_sr=4326, order_by="OBJECTID"
        )
        for f in page
    ]
    if not features:
        raise RuntimeError(f"{LAYER.base} returned no heritage items")

    items = []
    unnamed = offshore = 0
    for feature in features:
        attrs = feature.get("attributes") or {}
        name = clean(attrs.get("name"))
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if name is None or wkt is None:
            unnamed += 1
            continue
        lat, lon = esri_point(feature.get("geometry"))
        if in_bay(lat, lon):
            offshore += 1
            print(f"Hastings landmarks: {name!r} is pinned in Hawke Bay; skipped", file=sys.stderr)
            continue
        text = description_text(attrs.get("Description"))
        ref = REF_RE.search(text)
        ref = f"{ref.group(1)}{ref.group(2)}" if ref else None
        category = CATEGORY_RE.search(text)
        if name.lower().endswith("historic area"):
            lm_type = "Historic Area"
        elif category and category.group(1) in ("I", "II"):
            lm_type = f"Category {category.group(1)}"
        elif category:
            lm_type = category.group(1).capitalize()
        else:
            lm_type = None
        address = ADDRESS_RE.search(text)
        items.append({
            "name": name,
            "geometry_raw": wkt,
            "lm_type": lm_type,
            "register_number": ref,
            "address": clean(address.group(1)) if address else None,
        })

    refs = Counter(item["register_number"] for item in items if item["register_number"])
    for item in items:
        ref = item["register_number"]
        if ref is None:
            item["landmark_id"] = f"has-{slug(item['name'])}"
        elif refs[ref] > 1:
            item["landmark_id"] = f"has-{ref.lower()}-{slug(item['name'])}"
        else:
            item["landmark_id"] = f"has-{ref.lower()}"

    ids = [item["landmark_id"] for item in items]
    if len(set(ids)) != len(ids):
        raise RuntimeError("Hastings heritage items: repeated landmark_id")
    if unnamed:
        print(f"Hastings landmarks: skipped {unnamed} rows with no name or geometry", file=sys.stderr)
    n = len(items)
    print(
        f"Hastings landmarks: {n} District Plan heritage items ({offshore} offshore skipped)",
        file=sys.stderr,
    )
    return pa.table({
        "landmark_id": pa.array(ids, type=pa.string()),
        "name": pa.array([i["name"] for i in items], type=pa.string()),
        "city": pa.array(["NZHAS"] * n, type=pa.string()),
        "geometry_raw": pa.array([i["geometry_raw"] for i in items], type=pa.string()),
        "lm_type": pa.array([i["lm_type"] for i in items], type=pa.string()),
        "register_number": pa.array([i["register_number"] for i in items], type=pa.string()),
        "address": pa.array([i["address"] for i in items], type=pa.string()),
    })


if __name__ == "__main__":
    emit(build())

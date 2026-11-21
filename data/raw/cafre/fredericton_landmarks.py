#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Fredericton's heritage preservation areas.

Source: "Heritage View OD" (item 7a92b67c2593433196e35dc1f3e6e763) on the
City of Fredericton's ArcGIS Online organisation, owned by the City's planning
GIS account: the areas designated under the City's Heritage Preservation
By-law -- St. Anne's Point Heritage Preservation Area (the downtown and the
Garrison), Queen Square and St. Mary's.  Licence: the item states none and it
is not in the "Fredericton Open Data" catalogue group, so the City's Open
Government License does not visibly cover it (no licence stated; displayed
with attribution only, not licensed for reuse).

The City publishes no point register of its Local Historic Places, and the
Province's New Brunswick Register of Historic Places has no open layer; the
Directory of Federal Heritage Designations (open.canada.ca, OGL-Canada) lists
Fredericton's dozen national historic sites by street address only, so it
would need a geocoded, staged CSV.  The City's own designated areas are the
official source that is live and placed.

**One landmark per area.**  The layer stores St. Anne's Point as four polygons
(the main area and three outliers) sharing `HeritageArea = 'SAPHPA'`; their
rings are combined into one multipolygon.  The id is that area code
(`fre-hpa-{code}`), which survives a reload where the OBJECTIDs would not.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import emit

LAYER = FeatureLayer(
    "https://services2.arcgis.com/iLWAxhpxafhOza2U/arcgis/rest/services/"
    "Heritage_View_OD/FeatureServer/69",
    timeout=120,
)
OUT_FIELDS = "OBJECTID,Description,HeritageArea"


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


# The layer's `Description` is a short label for two of the three areas.
FULL_NAMES = {
    "Queen Square": "Queen Square Heritage Preservation Area",
    "St Marys": "St. Mary's Heritage Preservation Area",
}


def transform(features: list[dict]) -> pa.Table:
    areas: dict[str, dict] = {}
    for feature in features:
        rec = feature.get("attributes") or {}
        code = clean(rec.get("HeritageArea"))
        label = clean(rec.get("Description"))
        rings = (feature.get("geometry") or {}).get("rings") or []
        if code is None or label is None or not rings:
            continue
        area = areas.setdefault(code, {"name": FULL_NAMES.get(label, label), "rings": []})
        area["rings"].extend(rings)

    landmark_id: list[str] = []
    name: list[str] = []
    geometry_raw: list[str] = []
    for code, area in sorted(areas.items()):
        wkt = esri_geometry_to_wkt({"rings": area["rings"]})
        if wkt is None:
            continue
        landmark_id.append(f"fre-hpa-{code.lower()}")
        name.append(area["name"])
        geometry_raw.append(wkt)

    n = len(landmark_id)
    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(["CAFRE"] * n, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "property_type": pa.array(["Heritage Preservation Area"] * n, type=pa.string()),
        }
    )


if __name__ == "__main__":
    features: list[dict] = []
    for page in iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    ):
        features.extend(page)
    table = transform(features)
    print(
        f"Fredericton landmarks: {table.num_rows} area(s) from {len(features)} polygon(s)",
        file=sys.stderr,
    )
    if table.num_rows == 0:
        raise RuntimeError("Fredericton heritage areas produced no landmarks")
    emit(table)

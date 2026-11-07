#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Fairfax County's Inventory of Historic Sites, as Northern Virginia's landmarks.

Source: "Inventory of Historic Sites" on the Fairfax County GIS hub, 348
property polygons from the county's designation register (with National and
Virginia Register flags).  The 32 `DEMOLISHED = 'Yes'` rows are sites of
something no longer there and are dropped.  `HISTORIC_ID` repeats across
parcels of one property, so `GlobalID` is the grain.
"""

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
from shared.ingest import emit

LAYER = FeatureLayer(
    "https://services1.arcgis.com/ioennV6PpG5Xodq0/arcgis/rest/services/"
    "Historic_Sites_Inventory/FeatureServer/0"
)
OUT_FIELDS = "GlobalID,HIST_PROPERTY,RESOURCE_TYPE,CONST_DATE"
WHERE = "DEMOLISHED <> 'Yes' OR DEMOLISHED IS NULL"


def clean(value) -> str | None:
    text = " ".join(str(value).split()) if value is not None else ""
    return text.rstrip("*").strip() or None


def transform(features: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    name: list[str] = []
    geometry_raw: list[str] = []
    property_type: list[str | None] = []
    year_built: list[int | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        label = clean(rec.get("HIST_PROPERTY"))
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if not rec.get("GlobalID") or label is None or wkt is None:
            continue
        built = esri_ms_to_date(rec.get("CONST_DATE"))
        landmark_id.append(f"ffx-{rec['GlobalID'].strip('{}').lower()}")
        name.append(label)
        geometry_raw.append(wkt)
        property_type.append(clean(rec.get("RESOURCE_TYPE")))
        year_built.append(built.year if built else None)

    return pa.table({
        "landmark_id": pa.array(landmark_id, type=pa.string()),
        "name": pa.array(name, type=pa.string()),
        "city": pa.array(["USNVA"] * len(landmark_id), type=pa.string()),
        "geometry_raw": pa.array(geometry_raw, type=pa.string()),
        "property_type": pa.array(property_type, type=pa.string()),
        "year_built": pa.array(year_built, type=pa.int64()),
    })


if __name__ == "__main__":
    features: list[dict] = []
    for page in iter_features(LAYER, out_fields=OUT_FIELDS, where=WHERE, return_geometry=True):
        features.extend(page)
    emit(transform(features))

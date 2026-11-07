#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""City of Falls Church, VA public trees, from the city's ArcGIS Online org.

Source: "Public_Trees", 7,817 rows: the city's 2004 street, park, school and
facility inventory (`INV_DATE` is April 2004).  The layer was last edited in
2015 and is effectively a snapshot; it is still the only public inventory
inside the Beltway west of Arlington.

`SPECIES` is a botanical name.  `VACANT = 'V'` marks the 779 planting sites
(`Vacant Site: Small/Medium/Large`); they are dropped here because the flag is
the source's own, and the shared rule also drops `Stump`.  `CONDITION = 'Dead'`
rows are standing trees and stay.  `SITE_ID` is unique over the whole layer.
`INV_DATE` is when a surveyor visited, not a planting date, so none is
published.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    emit,
    enforce_tree_schema,
    normalize_species,
    normalize_tree_name,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services1.arcgis.com/2hmXRAz4ofcdQP6p/arcgis/rest/services/"
    "Public_Trees/FeatureServer/0"
)
OUT_FIELDS = "SITE_ID,SPECIES,COMMON_NAM,DBH,VACANT"


def is_tree(feature: dict) -> bool:
    return (feature.get("attributes") or {}).get("VACANT") != "V"


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    tree_name: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        site_id = rec.get("SITE_ID")
        if site_id is None:
            continue
        lat, lon = esri_point(feature.get("geometry"))
        tree_id.append(f"fch-{site_id}")
        species.append(normalize_species(rec.get("SPECIES")))
        tree_name.append(normalize_tree_name(rec.get("COMMON_NAM")))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(rec.get("DBH"))

    plant_date: list[date | None] = [None] * len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["USNVA"] * len(tree_id), type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "tree_name": pa.array(tree_name, type=pa.string()),
        "plant_date": pa.array(plant_date, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


if __name__ == "__main__":
    pages = iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, order_by="FID"
    )
    table = stream_to_table(pages, transform, keep=is_tree, label="Falls Church Public_Trees")
    table = validate_coordinates(table, city="Northern Virginia", city_code="USNVA")
    table = enforce_tree_schema(table, city="Northern Virginia", data_source="FALLS_CHURCH")
    emit(table)

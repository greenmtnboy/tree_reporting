#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Arlington County, VA's public tree inventory, from the county's ArcGIS Hub.

Source: "DPR Trees" on `gisdata-arlgis.opendata.arcgis.com`, 30,907 rows,
edited daily from the county's Cartegraph asset system (street, median, park
and school trees; `Ownership` names VDOT and state rights-of-way too).

**The species is a common name only**, USDA-style and comma-inverted (`Oak,
Willow`, `Tuliptree`); there is no botanical column.  `shared.species.english`
resolves it.  `X, Other` is Arlington's "some X" and resolves to the genus.

`ID` is the Cartegraph asset id, unique and non-null over all 30,907 rows.
`Retired` is null on every row and `Inactive` is 0 on every row, so the layer
publishes only standing assets.  `Installed` is a planting date; a scheduled
planting carries a future one, which `enforce_tree_schema` nulls.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_date, esri_point, iter_features
from shared.ingest import (
    emit,
    enforce_tree_schema,
    normalize_tree_name,
    stream_to_table,
    validate_coordinates,
)
from shared.species.english import species_from_common_name

LAYER = FeatureLayer(
    "https://services1.arcgis.com/JrtUzVcH8S2wnzVH/arcgis/rest/services/"
    "DPR_Trees/FeatureServer/0"
)
OUT_FIELDS = "ID,CommonName,DBHIn,Installed"

# Polygons of woodland recorded as one asset, not a tree.
NOT_A_TREE = frozenset({"wooded area"})

OTHER_SUFFIX = ", other"


def arlington_species(common_name: str | None) -> str | None:
    if not common_name:
        return None
    text = " ".join(common_name.split())
    if text.lower().endswith(OTHER_SUFFIX):
        text = text[: -len(OTHER_SUFFIX)]
    return species_from_common_name(text)


def is_tree(feature: dict) -> bool:
    name = (feature.get("attributes") or {}).get("CommonName") or ""
    return name.strip().lower() not in NOT_A_TREE


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    tree_name: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        asset_id = str(rec.get("ID") or "").strip()
        if not asset_id:
            continue
        lat, lon = esri_point(feature.get("geometry"))
        tree_id.append(f"arl-{asset_id}")
        species.append(arlington_species(rec.get("CommonName")))
        tree_name.append(normalize_tree_name(rec.get("CommonName")))
        plant_date.append(esri_ms_to_date(rec.get("Installed")))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(rec.get("DBHIn"))

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
    pages = iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True)
    table = stream_to_table(pages, transform, keep=is_tree, label="Arlington DPR Trees")
    table = validate_coordinates(table, city="Northern Virginia", city_code="USNVA")
    table = enforce_tree_schema(table, city="Northern Virginia", data_source="ARLINGTON")
    emit(table)

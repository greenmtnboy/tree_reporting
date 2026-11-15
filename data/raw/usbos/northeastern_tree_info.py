#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Northeastern University's Boston campus Arboretum, from the university's
ArcGIS Online org (NEUADMIN).

The layer behind the public "Northeastern University Arboretum Viewer"
experience: 2,193 plantings on the Fenway campus (2026-11-15), one point each,
with a binomial (cultivar quoted in the same field) and a common name.  No
diameter, height, crown or planting date is filled on any row, and
`Condition` is `Unknown` throughout, so the rows carry species and position
only; Boston imputes the dbh from the species average like any other source.

The grain is `GlobalID`: populated and distinct on every row.  `AssetID`
(`0044-2021*A`) is distinct too but is the grounds team's own label, which
they may renumber.

It is a curated arboretum, so it records shrubs and climbers as the Arnold
Arboretum's layer does, and keeps them.  It also records a handful of ferns
and herbaceous groundcovers, which are not woody plants at a point on the map;
those genera are dropped here with a logged count.
"""

import sys
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
    "https://services5.arcgis.com/S8KZ4xiwVgqpYP63/arcgis/rest/services/"
    "Trees/FeatureServer/3"
)

OUT_FIELDS = "OBJECTID,GlobalID,SpeciesType,CommonName,Diameter,Installed"

# Ferns and herbaceous perennials planted in the campus beds (maidenhair,
# wild ginger, painted fern, barrenwort, Christmas fern) and creeping thyme:
# none is a woody plant, so none belongs on a tree map.
NON_WOODY_GENERA = frozenset(
    {"adiantum", "asarum", "athyrium", "epimedium", "polystichum", "thymus"}
)


def tree_id_for(global_id: str | None) -> str | None:
    """`neu-` plus the GlobalID, unbraced and lower-case; None when missing so
    `enforce_tree_schema` refuses the build rather than inventing an id."""
    if not global_id:
        return None
    value = str(global_id).strip().strip("{}").lower()
    return f"neu-{value}" if value else None


def is_woody(feature: dict) -> bool:
    species = (feature.get("attributes") or {}).get("SpeciesType") or ""
    genus = species.strip().split(" ", 1)[0].lower()
    return genus not in NON_WOODY_GENERA


def transform(features: list[dict]) -> pa.Table:
    tree_ids: list[str | None] = []
    species: list[str | None] = []
    tree_names: list[str | None] = []
    latitudes: list[float | None] = []
    longitudes: list[float | None] = []
    dbhs: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        tree_ids.append(tree_id_for(rec.get("GlobalID")))
        # The quoted cultivar stays in: enforce_tree_schema reduces the
        # species to the taxon and keeps the selection on the tree row.
        species.append(normalize_species(rec.get("SpeciesType")))
        tree_names.append(normalize_tree_name(rec.get("CommonName")))
        lat, lon = esri_point(feature.get("geometry"))
        latitudes.append(lat)
        longitudes.append(lon)
        # `Diameter` is empty on every row today (2026-11-15); the viewer
        # gives it no unit, so read it as inches, the US campus convention.
        raw_dbh = rec.get("Diameter")
        dbhs.append(float(raw_dbh) if raw_dbh not in (None, 0) else None)

    n = len(tree_ids)
    return pa.table(
        {
            "tree_id": pa.array(tree_ids, type=pa.string()),
            "city": pa.array(["USBOS"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "tree_name": pa.array(tree_names, type=pa.string()),
            # `Installed` is empty on every row; `Inspected` is a survey
            # visit, not a planting, so it is not a plant_date.
            "plant_date": pa.array([None] * n, type=pa.date32()),
            "latitude": pa.array(latitudes, type=pa.float64()),
            "longitude": pa.array(longitudes, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbhs, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(
        iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True),
        transform,
        keep=is_woody,
        label="Northeastern Arboretum",
    )
    table = validate_coordinates(table, city="Northeastern", city_code="USBOS")
    table = enforce_tree_schema(table, city="Northeastern", data_source="NORTHEASTERN")
    emit(table)

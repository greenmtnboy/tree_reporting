#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Dedham, Massachusetts's tree inventory.

The Town's `Tree Inventory` feature layer (owner `DedhamGIS`,
`production_DBO_Trees`), about 8,400 sites, surveyed in 2017 with additions
since (`inv_date`).  No licence is stated on the item or the service:
displayed with attribution only, not licensed for reuse.

* **Not every site is a tree.**  `Species_bot` names vacant sites (`Vacant
  site small`, `Vacant - Retired/Obsolete` ...) and stumps; those rows are
  dropped, as is any row with a `DateRemoved`.
* `Species_bot` is the scientific name, `DBH` inches, `DatePlanted` the
  planting date (empty today).
* The layer is in Massachusetts State Plane; `out_sr` reprojects it.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_date, esri_point, iter_features  # noqa: E402
from shared.ingest import (  # noqa: E402
    emit,
    enforce_tree_schema,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services1.arcgis.com/me41C4ZTLmDO2rPK/arcgis/rest/services/"
    "production_DBO_Trees/FeatureServer/0"
)
OUT_FIELDS = "OBJECTID,Species_bot,DBH,DatePlanted,DateRemoved"


def is_tree(rec: dict) -> bool:
    name = " ".join(str(rec.get("Species_bot") or "").split()).lower()
    if name.startswith("vacant") or name == "stump":
        return False
    return rec.get("DateRemoved") is None


def iter_row_chunks():
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


def transform(features: list[dict]) -> pa.Table:
    ids, species, planted, lats, lons, dbhs = [], [], [], [], [], []
    for feature in features:
        rec = feature.get("attributes") or {}
        if not is_tree(rec):
            continue
        lat, lon = esri_point(feature.get("geometry"))
        ids.append(f"ded-{rec['OBJECTID']}")
        species.append(normalize_species(rec.get("Species_bot")))
        planted.append(esri_ms_to_date(rec.get("DatePlanted")))
        lats.append(lat)
        lons.append(lon)
        dbh = rec.get("DBH")
        dbhs.append(float(dbh) if dbh not in (None, 0) else None)
    return pa.table(
        {
            "tree_id": pa.array(ids, type=pa.string()),
            "city": pa.array(["USDED"] * len(ids), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array(planted, type=pa.date32()),
            "latitude": pa.array(lats, type=pa.float64()),
            "longitude": pa.array(lons, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbhs, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Dedham tree inventory")
    table = validate_coordinates(table, city="Dedham", city_code="USDED")
    table = enforce_tree_schema(table, city="Dedham", data_source="DEDHAM")
    emit(table)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Plainfield, Illinois's park trees.

The Plainfield Park District's `Plainfield Public Trees` feature layer
(ArcGIS Online, owner the district's planning office,
`Plainfield_PkD_Trees_1_14_21`): about 4,400 trees in the district's parks.
It is the park district's, not the village's: the village publishes no street
inventory (its `Planting2022` layer is a planting work list), so Plainfield's
street trees come from OSM and community submissions.  No licence is stated
on the item or the service: displayed with attribution only, not licensed for
reuse.

* **Every published row is a standing tree.**  `TREE_STATUS` has a domain
  with removed, stump and unknown values, but every row is `ACTIVE TREE`;
  any other status is dropped, so a removal the district starts publishing
  does not appear as a tree.
* `LATIN_NAME` is the scientific name, and it is the authority: the
  `COMMON_NAME` beside it disagrees on a few rows (`Cercis canadensis` /
  `HACKBERRY`).  One misspelling the shared map does not hold is fixed here.
* No DBH and no planting date.
* The layer is in Web Mercator; `out_sr` reprojects it.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features  # noqa: E402
from shared.ingest import (  # noqa: E402
    emit,
    enforce_tree_schema,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services8.arcgis.com/eOqUuAWRL4PqiMCt/arcgis/rest/services/"
    "Plainfield_Public_Trees/FeatureServer/0"
)
OUT_FIELDS = "OBJECTID,TREE_STATUS,LATIN_NAME"

LATIN_FIXES: dict[str, str] = {
    "Syrubga reticulata": "Syringa reticulata",
}


def species_name(value) -> str | None:
    text = " ".join(str(value or "").split())
    return normalize_species(LATIN_FIXES.get(text, text) or None)


def iter_row_chunks():
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


def transform(features: list[dict]) -> pa.Table:
    ids, species, lats, lons = [], [], [], []
    for feature in features:
        rec = feature.get("attributes") or {}
        if (rec.get("TREE_STATUS") or "").strip().upper() != "ACTIVE TREE":
            continue
        lat, lon = esri_point(feature.get("geometry"))
        ids.append(f"plf-{rec['OBJECTID']}")
        species.append(species_name(rec.get("LATIN_NAME")))
        lats.append(lat)
        lons.append(lon)
    n = len(ids)
    return pa.table(
        {
            "tree_id": pa.array(ids, type=pa.string()),
            "city": pa.array(["USPLF"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array([None] * n, type=pa.date32()),
            "latitude": pa.array(lats, type=pa.float64()),
            "longitude": pa.array(lons, type=pa.float64()),
            "diameter_at_breast_height": pa.array([None] * n, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Plainfield Park District trees")
    table = validate_coordinates(table, city="Plainfield", city_code="USPLF")
    table = enforce_tree_schema(table, city="Plainfield", data_source="PLAINFIELD_PARK_DISTRICT")
    emit(table)

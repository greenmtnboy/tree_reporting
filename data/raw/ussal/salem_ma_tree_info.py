#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Salem, Massachusetts's street trees.

The City's `Salem_Street_Trees` feature layer (owner `Salem_Massachusetts`,
credited to SalemGIS and Salem DPW), about 5,500 street sites surveyed from
2013 and last edited in 2020.  No licence is stated on the item or the
service: displayed with attribution only, not licensed for reuse.

* **Not every site is a tree.**  `Wood_Cond` 5 (Stump) and 6 (Empty Tree
  Pit), written either as the code or as its label, are dropped, and so are
  rows whose common name says the same (`Stump/Pit`, `Empty Tree Pit`).
* **`Sci_Name` is the species, typos and all**: the misspellings are folded
  centrally (`SPECIES_MISSPELLINGS`).  About 900 rows have no scientific name
  but a common one; `COMMON_NAMES` maps the ones the layer uses, and the rest
  stay `Unknown`.
* **`DBH` is a size class in inches**, stored either as the domain code or as
  its label (`6.0-12.0 in`); a class becomes its midpoint, the open top class
  its bound plus 3 in.  Class 1 (`0.0 in`) is unmeasured.
* The layer is in Massachusetts State Plane; `out_sr` reprojects it.
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
    "https://services9.arcgis.com/nPsFhwkdebYjxn1R/arcgis/rest/services/"
    "Salem_Street_Trees/FeatureServer/0"
)
OUT_FIELDS = "OBJECTID,Sci_Name,Tree_Name,DBH,Wood_Cond"

NOT_A_TREE_CONDITION = {"5", "6", "stump", "empty tree pit"}
NOT_A_TREE_NAME = {"stump/pit", "empty tree pit", "tree pit"}

# DBH class (code or label) -> midpoint, inches.
DBH_CLASS_IN: dict[str, float | None] = {
    "1": None, "0.0 in": None,
    "2": 1.5, "0.0-3.0 in": 1.5,
    "3": 4.5, "3.0-6.0 in": 4.5,
    "4": 9.0, "6.0-12.0 in": 9.0,
    "5": 15.0, "12.0-18.0 in": 15.0,
    "6": 21.0, "18.0-24.0 in": 21.0,
    "7": 27.0, "24.0-30.0 in": 27.0,
    "8": 33.0, "30.0-36.0 in": 33.0,
    "9": 39.0, "36.0-42.0 in": 39.0,
    "10": 45.0, ">42.0 in": 45.0,
}

# Common names on rows with no `Sci_Name`, as the layer spells them.
COMMON_NAMES: dict[str, str] = {
    "japanese tree lilac": "Syringa reticulata",
    "red maple": "Acer rubrum",
    "armstrong columnar red": "Acer rubrum",
    "zelkova": "Zelkova serrata",
    "hedge maple": "Acer campestre",
    "linden": "Tilia",
    "little leaf linden": "Tilia cordata",
    "little leaf linden greenspire": "Tilia cordata",
    "little leaf summersprite": "Tilia cordata",
    "little lead linden": "Tilia cordata",
    "norway maple": "Acer platanoides",
    "silver maple": "Acer saccharinum",
    "maple": "Acer",
    "chanticleer pear": "Pyrus calleryana",
    "bradford pear": "Pyrus calleryana",
    "aristocrat pear": "Pyrus calleryana",
    "london plane": "Platanus x acerifolia",
    "red oak": "Quercus rubra",
    "shademaster locust": "Gleditsia triacanthos",
    "ash": "Fraxinus",
    "elm accolade": "Ulmus",
    "ginko": "Ginkgo biloba",
}

# Values in `Sci_Name` that name no taxon the species rules can resolve.
NOT_A_SPECIES = {"n/a", "se", "ulmaceae"}
# A misspelt genus the near-pair audit (tools/species_audit.py) cannot pair,
# and two values that name only a genus.
SCI_NAME_FIXES = {
    "acre rubrum": "Acer rubrum",
    "prunus subhirtella or serrulata": "Prunus",
    "cercis cercideae": "Cercis",
}


def text(value) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def species_of(rec: dict) -> str | None:
    sci = text(rec.get("Sci_Name"))
    key = sci.lower()
    if key in SCI_NAME_FIXES:
        return SCI_NAME_FIXES[key]
    if sci and key not in NOT_A_SPECIES:
        return normalize_species(sci[0].upper() + sci[1:].lower())
    return COMMON_NAMES.get(text(rec.get("Tree_Name")).lower())


def dbh_of(value) -> float | None:
    key = text(value)
    if not key:
        return None
    if key not in DBH_CLASS_IN:
        raise ValueError(f"Salem: unrecognised DBH class {key!r}; the field's coding has changed")
    return DBH_CLASS_IN[key]


def is_tree(rec: dict) -> bool:
    return (
        text(rec.get("Wood_Cond")).lower() not in NOT_A_TREE_CONDITION
        and text(rec.get("Tree_Name")).lower() not in NOT_A_TREE_NAME
    )


def iter_row_chunks():
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


def transform(features: list[dict]) -> pa.Table:
    ids, species, lats, lons, dbhs = [], [], [], [], []
    for feature in features:
        rec = feature.get("attributes") or {}
        if not is_tree(rec):
            continue
        lat, lon = esri_point(feature.get("geometry"))
        ids.append(f"sal-{rec['OBJECTID']}")
        species.append(species_of(rec))
        lats.append(lat)
        lons.append(lon)
        dbhs.append(dbh_of(rec.get("DBH")))
    return pa.table(
        {
            "tree_id": pa.array(ids, type=pa.string()),
            "city": pa.array(["USSAL"] * len(ids), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "latitude": pa.array(lats, type=pa.float64()),
            "longitude": pa.array(lons, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbhs, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Salem MA street trees")
    table = validate_coordinates(table, city="Salem", city_code="USSAL")
    table = enforce_tree_schema(table, city="Salem", data_source="SALEM_MA")
    emit(table)

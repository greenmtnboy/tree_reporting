#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""MIT's Cambridge campus tree inventory, from the university's own ArcGIS
Online org (Facility Information Systems, `fisgis`).

No licence is stated on the layer or its service; it is published openly
under MIT's public basemap service and displayed here with attribution only,
not licensed for reuse.

`MIT_TREES` in the "Administrative Basemap Layers" FeatureServer is the
grounds team's live record: the summer-2024 Bartlett Tree Experts resurvey
(the crew field names a bartlett.com surveyor; field visits are dated July
and August 2024) in the Davey/TreeKeeper schema, kept up since (2,067 rows
updated 2026-04-14, removals noted through 2026).  2,674 rows on
2026-11-25, close to the 2,650 trees MIT's 2025 Town Gown Report counts.

Cambridge's own feed (`cambridge_tree_info.py`) republishes an older copy of
the same survey under `ownership = 'MIT'` and `M`-prefixed ids.  Measured
2026-11-25, 1,732 of this layer's trees sit under 1 m from one of those rows
and agree on the genus 94% of the time within 3 m; 494 of this layer's trees
(300 of them new plantings) have no city row within 10 m, and the city's rows
this layer lacks cluster where trees were taken out.  So this layer is the
campus's source and the Cambridge ingest drops the city's MIT rows.  Street
trees the city owns on the campus edge, which MIT surveys too, are resolved in
the model (`uscam/cambridge_tree_info.preql`): the city's row is kept.

Three columns say something other than their names:

* `RemovalDat` holds the 2024 survey visit dates on 1,600 rows, not removals.
  `Removal = 'Remove'` is a work recommendation (98 rows, most still
  standing).  A tree that is gone is `Active = '0'` (7 rows, with notes such
  as "Tree Removed 11.24.2025"); those are dropped.
* `Quantity` above 1 marks a planted group (a Taxus or Thuja screen) recorded
  at one point.  It is kept as one tree: the point is where the group stands.
* `AddTaxon` is a free-text supplement: quoted or capitalised cultivar names
  ('Princeton', 'Accolade', Fastigiata) mixed with descriptions ("variegated",
  "fastigiate", "var. japonica").  Only a capitalised name is read as the
  cultivar.

The grain is `TreeID`: populated, distinct, and the grounds team's tree
number.  `DaveyIDNum` is the 2017 Davey Resource Group survey's number,
filled on 1,160 rows only.
"""

import math
import sys
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    emit,
    enforce_tree_schema,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services5.arcgis.com/FXrk7QY7CN707pF1/arcgis/rest/services/"
    "NEW_Administrative_Basemap_Layers/FeatureServer/56"
)

DBH_FIELDS = ("DBH1", "DBH2", "DBH3", "DBH4", "DBH5", "DBH6")
OUT_FIELDS = ",".join(
    ("OBJECTID", "TreeID", "ScientName", "AddTaxon", "Active", "TreeDatePl", *DBH_FIELDS)
)

# Descriptions written into AddTaxon with a capital, not selections.
NOT_A_CULTIVAR = frozenset({"variegated", "varigated"})


def tree_id_for(raw: str | None) -> str | None:
    """`mit-` plus the grounds team's TreeID; None when missing so
    `enforce_tree_schema` refuses the build rather than inventing an id."""
    value = str(raw).strip() if raw is not None else ""
    return f"mit-{value}" if value else None


def is_standing(feature: dict) -> bool:
    """`Active = '0'` is a tree the grounds team has removed."""
    return str((feature.get("attributes") or {}).get("Active") or "").strip() != "0"


def cultivar_for(raw: str | None) -> str | None:
    """A capitalised AddTaxon is a cultivar name; a lower-case one is a
    description of the tree ("fastigiate", "var. japonica")."""
    value = (raw or "").strip().strip("'\"‘’“”").strip()
    if not value or not value[0].isupper() or value.lower() in NOT_A_CULTIVAR:
        return None
    return value


def dbh_for(rec: dict) -> float | None:
    """Inches at breast height; a multi-stem tree's stems combine as the
    square root of the sum of their squares, the basal-area equivalent."""
    stems: list[float] = []
    for field in DBH_FIELDS:
        try:
            value = float(str(rec.get(field) or "").strip())
        except ValueError:
            continue
        if value > 0:
            stems.append(value)
    if not stems:
        return None
    return round(math.sqrt(sum(s * s for s in stems)), 2)


def plant_date_for(raw: str | None) -> date | None:
    """`TreeDatePl` is MM/DD/YYYY, filled on the newest plantings only."""
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%m/%d/%Y").date()
    except ValueError:
        return None


def transform(features: list[dict]) -> pa.Table:
    tree_ids: list[str | None] = []
    species: list[str | None] = []
    cultivars: list[str | None] = []
    plant_dates: list[date | None] = []
    latitudes: list[float | None] = []
    longitudes: list[float | None] = []
    dbhs: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        tree_ids.append(tree_id_for(rec.get("TreeID")))
        species.append(normalize_species(rec.get("ScientName")))
        cultivars.append(cultivar_for(rec.get("AddTaxon")))
        plant_dates.append(plant_date_for(rec.get("TreeDatePl")))
        lat, lon = esri_point(feature.get("geometry"))
        latitudes.append(lat)
        longitudes.append(lon)
        dbhs.append(dbh_for(rec))

    n = len(tree_ids)
    return pa.table(
        {
            "tree_id": pa.array(tree_ids, type=pa.string()),
            "city": pa.array(["USCAM"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivars, type=pa.string()),
            "plant_date": pa.array(plant_dates, type=pa.date32()),
            "latitude": pa.array(latitudes, type=pa.float64()),
            "longitude": pa.array(longitudes, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbhs, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(
        iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True),
        transform,
        keep=is_standing,
        label="MIT campus trees",
    )
    table = validate_coordinates(table, city="MIT", city_code="USCAM")
    table = enforce_tree_schema(table, city="MIT", data_source="MIT")
    emit(table)

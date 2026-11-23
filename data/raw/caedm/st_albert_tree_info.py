#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""City of St. Albert's Tree Inventory, a municipal partition of CAEDM.

Source: "Tree Inventory" (ArcGIS Online item 4cd6fa57df0b44b895ca6dd69118f10a,
owner `open@stalbert.ca`), "an inventory of the City of St. Albert's owned and
maintained trees", 67,765 points; the layer is stored in NAD83(CSRS) 3TM 114
and reprojected server side (`outSR=4326`).  Licence: the Open Data Licence --
City of St. Albert v1.0 (https://data.stalbert.ca/pages/licence), adapted from
the Alberta Open Government Licence: copy, modify, publish and distribute,
commercial use included, with attribution.  St. Albert borders Edmonton to the
north-west and sits inside CAEDM's box, so it is a partition of Edmonton rather
than a city.  Edmonton's inventory stops at the city line.

The item says the layer "is refreshed weekly through ETL", i.e. reloaded whole,
and its OBJECTID is dense 1..67,765 -- a row number, not an id.

* **id** -- `GlobalID` is an ordinary GUID column carried over from the city's
  own system (the layer's `globalIdField` is empty, so ArcGIS does not mint
  it), so it survives the weekly reload.  On 2026-11-23 it was non-null and
  distinct on 64,588 rows; the 3,177 rows without one are the newest
  (OBJECTID 64,589 onwards).  Those are keyed on their rounded position
  (`coordinate_tree_id`), and a position two of them share is dropped by
  `drop_stacked_coordinates`.  A GUID id can never equal a coordinate id, so
  the two key spaces cannot collide.  Both are prefixed `stal-`.
* **species** -- `LatinName` is the scientific name, sometimes with a quoted
  cultivar (`Tilia flavescens 'Dropmore'`, moved out by the schema), a variety
  (`Fraxinus pennsylvanica lanceolata`), a trailing note (`Ulmus americana and
  cultivars`, `Picea pungens cvs`) or a parenthetical synonym (`Larix sibirica
  ( russica )`); `sanitize_species` truncates all of those to the binomial.
  8,116 rows have no `LatinName` and only a common name in `SpeciesCommon`
  ("Crabapple", "Spruce", "Ash - Black"); those go through the shared curated
  table (`shared.species.english`), mostly to genus rank.  One override: the
  layer's "Olive" is Russian olive -- all 115 of its "Olive" rows that carry a
  Latin name say `Elaeagnus angustifolia` -- where the shared table reads the
  word as `Olea europaea`.
* `Cultivar_Variety` is free text typed in the field ("Brandon", "Blue",
  "It's a tree", "16", "Suckers/bush") and is not mapped; the quoted cultivar
  in `LatinName` is the one the schema keeps.
* **dbh / crown / height** -- not published.
* **plant_date** -- `YrPlanted`, a year (null on 84% of rows), read as 1 January.
  Two years before the settlement's founding (1832, 1842) are slips and are
  nulled.
* There is no status column: every feature is a standing city tree (`VegType`
  is only Deciduous/Coniferous).  `LastInspectionDate`, `LastPruningDate`,
  `TreeType` and `Fruit_Tree` are not mapped.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.species.english import species_from_common_name
from shared.ingest import (
    coordinate_tree_id,
    drop_stacked_coordinates,
    emit,
    enforce_tree_schema,
    normalize_species,
    parse_plant_date_year,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services1.arcgis.com/fyyY0cNXvmUWvX1x/arcgis/rest/services/"
    "Tree_Inventory/FeatureServer/0",
    timeout=120,
)
OUT_FIELDS = "OBJECTID,GlobalID,LatinName,SpeciesCommon,YrPlanted"
ID_PREFIX = "stal"

# St. Albert was founded as a mission in 1861; an earlier planting year is a
# keying slip, not a tree.
FIRST_PLANTING_YEAR = 1861

# Common names this source uses differently from the shared table.
COMMON_NAME_OVERRIDES = {"olive": "Elaeagnus angustifolia"}


def clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_species(attrs: dict) -> str | None:
    latin = clean(attrs.get("LatinName"))
    if latin:
        return normalize_species(latin)
    common = clean(attrs.get("SpeciesCommon"))
    if not common:
        return None
    override = COMMON_NAME_OVERRIDES.get(common.lower())
    return override or species_from_common_name(common)


def parse_year(value) -> date | None:
    planted = parse_plant_date_year(value)
    if planted is None or planted.year < FIRST_PLANTING_YEAR:
        return None
    return planted


def guid(value) -> str | None:
    text = clean(value)
    return text.strip("{}").lower() if text else None


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        gid = guid(attrs.get("GlobalID"))
        if gid:
            tree_id.append(f"{ID_PREFIX}-{gid}")
        elif lat is not None and lon is not None:
            tree_id.append(coordinate_tree_id(ID_PREFIX, lon, lat))
        else:
            tree_id.append(None)
        species.append(parse_species(attrs))
        plant_date.append(parse_year(attrs.get("YrPlanted")))
        latitude.append(lat)
        longitude.append(lon)

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["CAEDM"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "plant_date": pa.array(plant_date, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array([None] * n, type=pa.float64()),
    })


if __name__ == "__main__":
    pages = iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True)
    table = stream_to_table(pages, transform, label="St. Albert Tree Inventory")
    if table.num_rows == 0:
        raise RuntimeError("St. Albert: the Tree Inventory layer returned no features")
    # A row with neither a GlobalID nor a position cannot be addressed.
    missing = table.column("tree_id").null_count
    if missing:
        print(
            f"St. Albert: dropped {missing} row(s) with no GlobalID and no position",
            file=sys.stderr,
        )
        table = table.filter(pc.is_valid(table.column("tree_id")))
    table = drop_stacked_coordinates(table, label="St. Albert")
    table = validate_coordinates(table, city="Edmonton", city_code="CAEDM")
    table = enforce_tree_schema(table, city="Edmonton", data_source="ST_ALBERT")
    emit(table)

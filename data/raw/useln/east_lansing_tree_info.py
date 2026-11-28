#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""East Lansing, Michigan's tree inventory.

The City's `TREES - CURRENT STATUS` feature layer (`TREES_STATUS`, on the
City's own ArcGIS server), behind its public web map "TREES - CITY OF EAST
LANSING PUBLIC": about 10,200 trees, surveyed by Davey (`dv*` columns) with
the City's later inspections and plantings.  No licence is stated on the item
or the service: displayed with attribution only, not licensed for reuse.

* **Every row is a tree.**  `Status` is a work state (`City Tree - Not Action
  Needed`, `Park Tree`, `New Planting`, `Needs Trimmed`, `Private Tree` ...),
  not a removal; no stump or vacant-site rows are published.
* `dvSPP` is the scientific name (216 spellings, cultivars and typos
  included; the shared normaliser folds most of them).  A handful of rows
  carry a common name instead; `COMMON_NAMES` maps those.
* **DBH is a band in inches** (`0-4 inches` ... `36+ inches`).  Each band
  publishes its midpoint, the open `36+` its bound plus half the width of the
  band below (39 in), Barrie's rule.
* `PlantDate` is the planting date (about 800 rows, the City's own plantings).
* The layer is in Michigan State Plane; `out_sr` reprojects it.
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
    "https://gis2.cityofeastlansing.com/arcgis/rest/services/TREES_STATUS/FeatureServer/0"
)
OUT_FIELDS = "OBJECTID,GlobalID,dvSPP,dvDBH,PlantDate"

# `dvDBH` band -> diameter midpoint, inches.
DBH_BAND_INCHES: dict[str, float] = {
    "0-4 inches": 2.0,
    "4-8 inches": 6.0,
    "8-12 inches": 10.0,
    "12-18 inches": 15.0,
    "18-24 inches": 21.0,
    "24-30 inches": 27.0,
    "30-36 inches": 33.0,
    "36+ inches": 39.0,
}

# The few rows whose `dvSPP` is a common name, lower-cased and trimmed.
COMMON_NAMES: dict[str, str] = {
    "maple": "Acer",
    "dogwood": "Cornus",
    "scotch pine": "Pinus sylvestris",
    "london plane": "Platanus x hispanica",
    "japanese tree liliac": "Syringa reticulata",
}


def species_name(value) -> str | None:
    text = " ".join(str(value or "").split())
    return normalize_species(COMMON_NAMES.get(text.lower(), text) or None)


def diameter_inches(value) -> float | None:
    text = " ".join(str(value or "").split())
    if not text:
        return None
    if text not in DBH_BAND_INCHES:
        raise ValueError(f"unknown East Lansing DBH band {value!r}")
    return DBH_BAND_INCHES[text]


def iter_row_chunks():
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


def transform(features: list[dict]) -> pa.Table:
    ids, species, planted, lats, lons, dbhs = [], [], [], [], [], []
    for feature in features:
        rec = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        ids.append(f"eln-{rec['OBJECTID']}")
        species.append(species_name(rec.get("dvSPP")))
        planted.append(esri_ms_to_date(rec.get("PlantDate")))
        lats.append(lat)
        lons.append(lon)
        dbhs.append(diameter_inches(rec.get("dvDBH")))
    return pa.table(
        {
            "tree_id": pa.array(ids, type=pa.string()),
            "city": pa.array(["USELN"] * len(ids), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array(planted, type=pa.date32()),
            "latitude": pa.array(lats, type=pa.float64()),
            "longitude": pa.array(lons, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbhs, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="East Lansing tree inventory")
    table = validate_coordinates(table, city="East Lansing", city_code="USELN")
    table = enforce_tree_schema(table, city="East Lansing", data_source="EAST_LANSING")
    emit(table)

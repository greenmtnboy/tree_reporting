#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Lethbridge's municipal tree inventory, from the city's ArcGIS Hub.

Source: "Trees" (`OpenData/odl_trees`) on `opendata.lethbridge.ca`, 45,433
rows.  Paging, the freshness watermark and Esri's epoch-milliseconds live in
`_arcgis_shared`.

**The cleanest schema of the Canadian ArcGIS set.**  Lethbridge publishes
`genus`, `species` and `cultivar` as three separate columns as well as the
combined `botn_name`, which is the split `enforce_tree_schema` wants and
almost nobody supplies.  The ingest still reads `botn_name` for the taxon --
it is the column the city curates, and `sanitize_species` already reduces its
appended cultivars correctly (`Ulmus americana Brandon` -> `Ulmus americana`,
measured across all 199 distinct values) -- and takes the cultivar from the
dedicated column, where it is authoritative rather than parsed.

**`AssetID` is a real per-tree key**: 45,433 distinct values across 45,433
rows, none null.  It is a synthetic `Tree_N` rather than a field number, but
it is the publisher's own and survives republication, which `OBJECTID` does
not.

**Retired trees are dropped** -- 231 rows whose `status` is `Retired`, a tree
the city has removed.  Two more carry no status at all and are kept: a missing
status is not a statement that the tree is gone.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, iter_features
from _ingest_shared import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    normalize_species,
    normalize_tree_name,
    parse_plant_date_year,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://gis.lethbridge.ca/gisopendata/rest/services/OpenData/"
    "odl_trees/MapServer/0",
    timeout=180,
)

# `X_Coord`/`Y_Coord` are the layer's own 3TM projection rather than lat/lon,
# so the geometry is what carries a usable position; `out_sr=4326` reprojects
# it server-side.
OUT_FIELDS = "AssetID,botn_name,cultivar,comn_name,diameter,planted,status"

# `diameter` is centimetres, as every Canadian portal publishes.  19 rows
# record 0, which is an unmeasured tree rather than one of no width; the top
# of the column is 248 cm, which is a real cottonwood.
MIN_DBH_CM = 1.0
MAX_DBH_CM = 300.0

# The one status that means the tree is gone.  Matched exactly rather than by
# prefix: `Private Maintenance` is a standing tree the city does not prune.
RETIRED_STATUS = "Retired"


def parse_dbh(value) -> float | None:
    """Centimetres to inches, with the unmeasured ends dropped to null."""
    try:
        cm = float(value)
    except (TypeError, ValueError):
        return None
    if cm < MIN_DBH_CM or cm > MAX_DBH_CM:
        return None
    return cm_to_inches(cm)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def is_standing(feature: dict) -> bool:
    """False for a tree the inventory has retired."""
    status = (feature.get("attributes") or {}).get("status")
    return (status or "").strip() != RETIRED_STATUS


def iter_row_chunks():
    """One ArcGIS page at a time."""
    return iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    )


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    tree_name: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        geom = feature.get("geometry") or {}
        raw_id = str(rec.get("AssetID") or "").strip()
        if not raw_id:
            continue

        tree_id.append(f"let-{raw_id}")
        species.append(normalize_species(rec.get("botn_name")))
        cultivar.append(clean(rec.get("cultivar")))
        tree_name.append(normalize_tree_name(rec.get("comn_name")))
        plant_date.append(parse_plant_date_year(rec.get("planted")))
        latitude.append(geom.get("y"))
        longitude.append(geom.get("x"))
        dbh.append(parse_dbh(rec.get("diameter")))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CALET"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "tree_name": pa.array(tree_name, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(
        iter_row_chunks(), transform, keep=is_standing, label="Lethbridge OpenData"
    )
    table = validate_coordinates(table, city="Lethbridge", city_code="CALET")
    table = enforce_tree_schema(
        table, city="Lethbridge", data_source="LETHBRIDGE_OPENDATA"
    )
    emit(table)

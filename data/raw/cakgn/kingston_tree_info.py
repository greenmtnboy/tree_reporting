#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Kingston's city-owned tree inventory, from the city's ArcGIS Hub.

Source: "City Owned Trees" (`Eng/City_Owned_Trees`) on
`opendatakingston.cityofkingston.ca`, 55,891 rows.  Paging, the freshness
watermark and Esri's epoch-milliseconds live in `_arcgis_shared`.

**`TREE_ID` is a real per-tree key**: 55,891 distinct values across 55,891
rows, none null, none blank, checked over the whole table.  `GLOBALID` is
equally clean and would have done; `TREE_ID` is the city's own name for the
tree, so a check-in recorded against it means something outside this repo.

**`DBH_TRUNK` uses 999 as its "not measured" sentinel** -- 2,992 rows, and the
only value above 200 cm.  Published as a diameter it would be a 9.8-metre
trunk, and it lands in the middle of every size chart; published as null it
says what the city actually knows.  A recorded 0 (162 rows) is the same thing
spelled differently.

**Retired trees are dropped.**  8,945 rows carry a `RETIRED_DATE`, which is
how this inventory records a tree that has been removed -- the row stays so
the asset history survives.  There is nothing at that point to put on a map,
so the ingest drops them, the same call every other city makes about a stump
or a vacant planting site.
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
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://utility.arcgis.com/usrsvcs/servers/"
    "511fd5299053486daf48c6466332320c/rest/services/Eng/City_Owned_Trees/"
    "FeatureServer/0",
    timeout=180,
)

# No lat/lon columns, so the geometry carries the position and `out_sr=4326`
# reprojects it out of the layer's Web Mercator storage.
OUT_FIELDS = "TREE_ID,SCIENTIFIC_NAME,COMMON_NAME,DBH_TRUNK,RETIRED_DATE"

# 999 is the layer's "not measured" sentinel and 0 is the same thing written
# differently; nothing real sits between 200 cm and 999.
MIN_DBH_CM = 1.0
MAX_DBH_CM = 200.0


def parse_dbh(value) -> float | None:
    """Centimetres to inches, with the sentinels dropped to null."""
    try:
        cm = float(value)
    except (TypeError, ValueError):
        return None
    if cm < MIN_DBH_CM or cm > MAX_DBH_CM:
        return None
    return cm_to_inches(cm)


def is_standing(feature: dict) -> bool:
    """False for a tree the inventory has retired.

    Passed to `stream_to_table` as its `keep` filter rather than applied inside
    `transform`, so the dropped rows never occupy a column and the run reports
    how many it dropped.
    """
    return (feature.get("attributes") or {}).get("RETIRED_DATE") is None


def iter_row_chunks():
    """One ArcGIS page at a time.

    A generator rather than a bulk read, which is the house rule for a paged
    source: holding every feature as a response body plus a dict each is what
    OOM-killed Washington DC's 2 GiB container at 216k.
    """
    return iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    )


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    tree_name: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        geom = feature.get("geometry") or {}
        raw_id = str(rec.get("TREE_ID") or "").strip()
        if not raw_id:
            continue

        tree_id.append(f"kgn-{raw_id}")
        species.append(normalize_species(rec.get("SCIENTIFIC_NAME")))
        tree_name.append(normalize_tree_name(rec.get("COMMON_NAME")))
        # The inventory records no planting date.  Still a typed date32 column:
        # an untyped pa.null() lands in the parquet as INT32 and breaks year().
        plant_date.append(None)
        latitude.append(geom.get("y"))
        longitude.append(geom.get("x"))
        dbh.append(parse_dbh(rec.get("DBH_TRUNK")))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CAKGN"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "tree_name": pa.array(tree_name, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(
        iter_row_chunks(), transform, keep=is_standing, label="Kingston OpenData"
    )
    table = validate_coordinates(table, city="Kingston", city_code="CAKGN")
    table = enforce_tree_schema(
        table, city="Kingston", data_source="KINGSTON_OPENDATA"
    )
    emit(table)

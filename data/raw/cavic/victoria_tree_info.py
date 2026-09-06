#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Victoria's tree inventory, from the city's ArcGIS Hub.

Source: "Tree Species (Parks trees database)"
(`OpenData/OpenData_Parks/MapServer/15`) on `opendata.victoria.ca`, 34,981
rows.  Paging, the freshness watermark and Esri's epoch-milliseconds live in
`_arcgis_shared`.

**The dataset title undersells it.**  It sits under the Parks service and
says "Parks trees database", but `TreeCategory` shows it is the whole
municipal inventory: 14,006 park trees, 9,888 boulevard, 7,125 setback, 1,619
hardscape, plus medians, frontages and 58 private trees the city tracks.  All
of them are published -- the category is what the city maintains the tree
under, not a statement about whether it exists.

**`SiteID` is a real per-tree key**: 34,981 distinct values across 34,981
rows, none null.  The layer also carries a `Site` column, which looks like an
id and is not -- 5,913 distinct values over the same rows, with `1` on 11,603
of them.  That is the DC `FACILITYID` trap in miniature, and checking the
whole column rather than a first page is what separates the two.

**`Species` and `BotanicalName` are the same column twice** -- zero rows
differ, measured with a server-side `Species <> BotanicalName` count.
`BotanicalName` is read because it is the one that says what it holds.
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
    "https://maps.victoria.ca/server/rest/services/OpenData/"
    "OpenData_Parks/MapServer/15",
    timeout=180,
)

# No lat/lon columns, so the geometry carries the position; `out_sr=4326`
# reprojects it server-side out of the layer's BC Albers storage.
OUT_FIELDS = "SiteID,BotanicalName,CommonName,DiameterAtBreastHeight,TreeCategory"

# Centimetres, as every Canadian portal publishes.  21 rows record 0, which is
# an unmeasured tree rather than one of no width; the top of the column is
# 267 cm, which on southern Vancouver Island is an ordinary Douglas-fir.
MIN_DBH_CM = 1.0
MAX_DBH_CM = 400.0


def parse_dbh(value) -> float | None:
    """Centimetres to inches, with the unmeasured ends dropped to null."""
    try:
        cm = float(value)
    except (TypeError, ValueError):
        return None
    if cm < MIN_DBH_CM or cm > MAX_DBH_CM:
        return None
    return cm_to_inches(cm)


def iter_row_chunks():
    """One ArcGIS page at a time."""
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
        raw_id = str(rec.get("SiteID") or "").strip()
        if not raw_id:
            continue

        tree_id.append(f"vic-{raw_id}")
        species.append(normalize_species(rec.get("BotanicalName")))
        tree_name.append(normalize_tree_name(rec.get("CommonName")))
        # The inventory records no planting date.  Still a typed date32 column:
        # an untyped pa.null() lands in the parquet as INT32 and breaks year().
        plant_date.append(None)
        latitude.append(geom.get("y"))
        longitude.append(geom.get("x"))
        dbh.append(parse_dbh(rec.get("DiameterAtBreastHeight")))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CAVIC"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "tree_name": pa.array(tree_name, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Victoria OpenData")
    table = validate_coordinates(table, city="Victoria", city_code="CAVIC")
    table = enforce_tree_schema(
        table, city="Victoria", data_source="VICTORIA_OPENDATA"
    )
    emit(table)

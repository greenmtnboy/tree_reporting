#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""New Westminster's tree inventory, from the city's ArcGIS Hub.

Source: "Tree Inventory (Active Trees)" (`Tree_Inventory_(PROD)_4_view`) on
`opendata.newwestcity.ca`, 16,111 rows.  Paging, the freshness watermark and
Esri's epoch-milliseconds live in `_arcgis_shared`.

**`globalid` is the key**, and it is the one this layer guarantees: 16,111
distinct values across 16,111 rows, none null, checked over the whole table.
The layer publishes no asset number of its own -- `objectid` is a row number
-- so the Esri GlobalID, which is assigned once per feature and preserved
across replication, is both the best available and the one `EXTENDING.md`
names first.

**`FULL_NAME` is the taxon, and it is already assembled.**  `GENUS` and
`SPECIES` are published too, in capitals and with more nulls (566 and 814
against `FULL_NAME`'s 618), and `FULL_NAME` is the column the city curates:
`Prunus cerasifera 'Pissardii Nigra'`, quoted cultivar and all.
`sanitize_species` takes the taxon off it and `CULTIVAR` supplies the
selection from its own column, where it is authoritative rather than parsed.

**`DBH` is centimetres with a long tail of typos** -- five rows above 200 cm,
one of them 3,432, which is a decimal-point error rather than a 34-metre
trunk.  3,876 rows record no diameter at all, which is a quarter of the city.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, esri_ms_to_date, iter_features
from _ingest_shared import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services3.arcgis.com/A7O8YnTNtzRPIn7T/arcgis/rest/services/"
    "Tree_Inventory_(PROD)_4_view/FeatureServer/0",
    timeout=180,
)

# No lat/lon columns, so the geometry carries the position; `out_sr=4326`
# reprojects it server-side out of the layer's Web Mercator storage.
OUT_FIELDS = "globalid,FULL_NAME,CULTIVAR,DBH,PLANTINGDATE"

# The layer's own OID field is lower-case, and `order_by` has to name it:
# offset paging over an unordered ArcGIS result can repeat or skip rows.
OID_FIELD = "objectid"

# Centimetres.  17 rows record 0, which is an unmeasured tree rather than one
# of no width; above 200 cm there are five rows and one of them is 3,432.
MIN_DBH_CM = 1.0
MAX_DBH_CM = 300.0


def parse_dbh(value) -> float | None:
    """Centimetres to inches, with the implausible ends dropped to null."""
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


def iter_row_chunks():
    """One ArcGIS page at a time."""
    return iter_features(
        LAYER,
        out_fields=OUT_FIELDS,
        return_geometry=True,
        out_sr=4326,
        order_by=OID_FIELD,
    )


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        geom = feature.get("geometry") or {}
        raw_id = str(rec.get("globalid") or "").strip()
        if not raw_id:
            continue

        tree_id.append(f"nwe-{raw_id}")
        species.append(normalize_species(rec.get("FULL_NAME")))
        cultivar.append(clean(rec.get("CULTIVAR")))
        plant_date.append(esri_ms_to_date(rec.get("PLANTINGDATE")))
        latitude.append(geom.get("y"))
        longitude.append(geom.get("x"))
        dbh.append(parse_dbh(rec.get("DBH")))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CANWE"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    # The layer publishes no common name, so `tree_name` is left off entirely
    # rather than emitted as an all-null column -- the tree card falls back to
    # the enrichment table's common name for the species, which is what it does
    # for every city that has one.
    table = stream_to_table(
        iter_row_chunks(), transform, label="New Westminster OpenData"
    )
    table = validate_coordinates(
        table, city="New Westminster", city_code="CANWE"
    )
    table = enforce_tree_schema(
        table, city="New Westminster", data_source="NEWWESTMINSTER_OPENDATA"
    )
    emit(table)

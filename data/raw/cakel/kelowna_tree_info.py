#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Kelowna's municipal tree inventory, from the city's ArcGIS Hub.

Source: "Tree Inventory" (`OpenData_Environment/MapServer/17`) on
`opendata.kelowna.ca`, 24,599 rows.  Paging, the freshness watermark and
Esri's epoch-milliseconds live in `_arcgis_shared`.

**Kelowna publishes no usable tree id, and this ingest keys on `OBJECTID`.**
That is the last resort in `EXTENDING.md`'s preference order, and it is taken
deliberately, so here is the measurement behind it.  The layer has exactly two
candidates.  `SITE_ID` looks like the id and is not: over the whole 24,599
rows it holds 21,190 distinct values, **3,214 of them null** and 195 rows
sharing an id with another tree -- one value covers 33 rows.  A null `tree_id`
silently drops its row from the parquet and a repeat fans out every join built
on the grain, so `enforce_tree_schema` refuses both; publishing on `SITE_ID`
would have meant losing 13% of the city and inflating the rest.  There is no
`GLOBALID` on the layer and no FeatureServer twin that has one (the service
directory lists `OpenData_Environment` as MapServer only), so `OBJECTID` is
what remains.

What that costs is worth stating plainly, because it is the same exception
Longueuil's synthesised coordinate id is: `OBJECTID` is a local row number,
so a rebuild on the publisher's side can reassign it, and a community check-in
recorded against `kel-1234` would then point at a different tree.  It is
unique and non-null today, which is more than `SITE_ID` manages.  If Kelowna
ever publishes a stable per-tree id, switch to it and accept the one-time
churn.

**Species comes from `Genus` + `Species`, not a combined column** -- the layer
splits them, and `CultivarOrVariety` carries the selection separately, which
is the split `enforce_tree_schema` wants and few portals supply.
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
    normalize_species_parts,
    normalize_tree_name,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://geoportal.kelowna.ca/arcgis/rest/services/ArcGISOnline/"
    "OpenData_Environment/MapServer/17",
    timeout=180,
)

# No lat/lon columns, so the geometry carries the position; `out_sr=4326`
# reprojects it server-side out of the layer's BC Albers storage.
OUT_FIELDS = "OBJECTID,Genus,Species,CultivarOrVariety,CommonName,dbh_cm,Status"

# The column is named for its unit and is centimetres, like every Canadian
# portal.  One row records 0, which is an unmeasured tree rather than one of
# no width; the top of the column is 443 cm, a plausible Okanagan cottonwood.
MIN_DBH_CM = 1.0
MAX_DBH_CM = 500.0


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
        oid = rec.get("OBJECTID")
        if oid is None:
            continue

        tree_id.append(f"kel-{oid}")
        species.append(normalize_species_parts(rec.get("Genus"), rec.get("Species")))
        cultivar.append(clean(rec.get("CultivarOrVariety")))
        tree_name.append(normalize_tree_name(rec.get("CommonName")))
        # `InventoryDate` is when a surveyor visited and `WateringDate` when
        # the tree was last watered.  Neither is a planting date, so Kelowna
        # publishes none -- still a typed date32 column, because an untyped
        # pa.null() lands in the parquet as INT32 and breaks year().
        plant_date.append(None)
        latitude.append(geom.get("y"))
        longitude.append(geom.get("x"))
        dbh.append(parse_dbh(rec.get("dbh_cm")))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CAKEL"] * len(tree_id), type=pa.string()),
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
    # `Status` is 'A' on all 24,599 rows, so there is nothing to filter on it.
    # The 16 rows whose species reads "Vacant" are dropped by
    # `enforce_tree_schema` instead -- see `is_not_a_tree`.
    table = stream_to_table(iter_row_chunks(), transform, label="Kelowna OpenData")
    table = validate_coordinates(table, city="Kelowna", city_code="CAKEL")
    table = enforce_tree_schema(
        table, city="Kelowna", data_source="KELOWNA_OPENDATA"
    )
    emit(table)

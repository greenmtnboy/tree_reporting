#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Toronto's street tree inventory, from the city's CKAN portal.

Source: "Street Tree Data" on `ckan0.cf.opendata.inter.prod-toronto.ca`,
resource `3dafa392`, 688,335 rows -- the largest single municipal inventory on
the map.  Paging, the freshness watermark and the point shape all live in
`_ckan_shared`.

Three things about this source are worth knowing:

**`STRUCTID` is the per-tree id, and it is a good one.**  Checked over the
whole table rather than a first page, the way `EXTENDING.md` asks: 688,335
distinct values across 688,335 rows, none null, none blank.  `OBJECTID` is
also present and is the datastore's local row number, which a republish can
reassign -- Washington DC shipped for months on a similarly plausible-looking
column that turned out not to be per-tree.

**The portal writes the literal string `"None"` where a value is missing.**
Its ETL renders Python's `None` rather than an empty field, so `SUFFIX` is the
four characters `None` on 683,023 rows, and `BOTANICAL_NAME` and `COMMON_NAME`
are on 56.  Nothing here special-cases it and nothing needs to: `"None"` is
already in `_SPECIES_PLACEHOLDERS`, so `sanitize_species` drops it, and in
`_UNKNOWN_COMMON_NAMES`, so `normalize_tree_name` does too.  It is called out
because the columns this ingest does *not* read are full of it, and the next
person to reach for `ADDRESS` or `SITE` will need to know.

**`DBH_TRUNK` is centimetres**, as every Canadian portal publishes, and the
column's top end is not a measurement: 39 rows record more than 3 m and the
largest is 93.8 m, which is a decimal-point error rather than a tree.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ckan_shared import CkanResource, iter_datastore_rows, point_lon_lat
from _ingest_shared import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    normalize_species,
    normalize_tree_name,
    stream_to_table,
    validate_coordinates,
)

RESOURCE = CkanResource(
    "ckan0.cf.opendata.inter.prod-toronto.ca",
    "3dafa392-c6ab-4f37-9bf9-21ddf7308eaf",
    timeout=180,
)

# `geometry` is a GeoJSON point held in a `text` column, so it arrives as a
# string.  The address columns are weight this ingest does not use.
FIELDS = "STRUCTID,BOTANICAL_NAME,COMMON_NAME,DBH_TRUNK,geometry"

# A recorded 0 would be an unmeasured tree rather than one of no width; the
# column has none.  The top end does have 39 rows above 3 m, up to 9,380 cm.
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


def iter_row_chunks():
    """One CKAN datastore page at a time.

    A generator, not a bulk read: at 688k rows this is the largest inventory
    here, and holding it as dicts is the whole-city peak that OOM-killed
    Washington DC's 2 GiB container at 216k features.
    """
    return iter_datastore_rows(RESOURCE, fields=FIELDS)


def transform(rows: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    tree_name: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for rec in rows:
        raw_id = str(rec.get("STRUCTID") or "").strip()
        if not raw_id:
            continue
        lon, lat = point_lon_lat(rec.get("geometry"))

        tree_id.append(f"tor-{raw_id}")
        species.append(normalize_species(rec.get("BOTANICAL_NAME")))
        # "Oak, swamp white" -- inverted so it sorts by genus, which
        # normalize_tree_name un-inverts into "Swamp white oak".
        tree_name.append(normalize_tree_name(rec.get("COMMON_NAME")))
        # The inventory records no planting date.  Still a typed date32 column:
        # an untyped pa.null() lands in the parquet as INT32 and breaks year().
        plant_date.append(None)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(parse_dbh(rec.get("DBH_TRUNK")))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CATOR"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "tree_name": pa.array(tree_name, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Toronto OpenData")
    table = validate_coordinates(table, city="Toronto", city_code="CATOR")
    table = enforce_tree_schema(
        table, city="Toronto", data_source="TORONTO_OPENDATA"
    )
    emit(table)

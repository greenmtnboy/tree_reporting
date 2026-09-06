#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Winnipeg's municipal tree inventory, from the city's Socrata portal.

Source: "Tree Inventory" (`hfwk-jp4h`) on data.winnipeg.ca, 305,385 rows.
Paging, the freshness watermark and the point-column shapes all live in
`_socrata_shared`.

This is the cleanest of the three Canadian Socrata sources: the portal already
publishes a per-tree `tree_id` (305,385 distinct, none null), a Latin
`botanical_name`, and a `diameter_at_breast_height` -- the canonical column
names almost verbatim.

Two things it leaves to us.  The diameter is **centimetres** despite the name,
which every Canadian portal does and the canonical column does not; and the
cultivar is written quoted inside the botanical name (`Ulmus americana
'Brandon'`), which `enforce_tree_schema` lifts onto the tree row by itself --
so there is no cultivar handling here, deliberately.  `sanitize_species` does
the rest: `Tilia spp.` truncates to the genus and
`Fraxinus pennsylvanica var. subintegerrima`, the city's single most common
tree at 81,929 rows, to the binomial the enrichment table is keyed on.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    normalize_species,
    normalize_tree_name,
    stream_to_table,
    validate_coordinates,
)
from _socrata_shared import SocrataDataset, iter_rows, point_lon_lat

DATASET = SocrataDataset("data.winnipeg.ca", "hfwk-jp4h", timeout=180)

# `point` is a GeoJSON object; `x`/`y` are UTM 14N and would need reprojecting.
SELECT = "tree_id,botanical_name,common_name,diameter_at_breast_height,point"

# 134 rows record a diameter of 0, which is an unmeasured tree rather than a
# tree of no width, and 35 record more than 3 m -- the largest 35.52 m, which
# is four times the widest tree on earth.  Both ends are dropped to null: the
# row is still a tree at a known location, it just has no usable size.
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
    """One Socrata page at a time.

    A generator, not a bulk read: `$limit=1000000` does return all 305,385
    rows in one response, which is exactly the whole-city peak that OOM-killed
    Washington DC's 2 GiB container at 216k.
    """
    return iter_rows(DATASET, select=SELECT)


def transform(rows: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    tree_name: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for rec in rows:
        raw_id = (rec.get("tree_id") or "").strip()
        if not raw_id:
            continue
        lon, lat = point_lon_lat(rec.get("point"))

        tree_id.append(f"wpg-{raw_id}")
        species.append(normalize_species(rec.get("botanical_name")))
        # The column mixes casing ("Colorado blue spruce", "silver maple").
        tree_name.append(normalize_tree_name(rec.get("common_name")))
        # The inventory records no planting date.  Still a typed date32 column:
        # an untyped pa.null() lands in the parquet as INT32 and breaks year().
        plant_date.append(None)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(parse_dbh(rec.get("diameter_at_breast_height")))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CAWPG"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "tree_name": pa.array(tree_name, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Winnipeg OpenData")
    table = validate_coordinates(table, city="Winnipeg", city_code="CAWPG")
    table = enforce_tree_schema(
        table, city="Winnipeg", data_source="WINNIPEG_OPENDATA"
    )
    emit(table)

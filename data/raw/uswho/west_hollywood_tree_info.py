#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""City of West Hollywood's City Tree Inventory (USWHO).

A partition of Los Angeles's model (USLAX) until 2026-11, when West Hollywood
became a city of its own; the `data_source` and the `weh-` ids are unchanged.

Source: Socrata dataset `qqwt-wx9z` on data.weho.org, "City Tree Inventory"
("an inventory of all City-managed trees within West Hollywood"), CC0 1.0.
9,105 rows.  `rowsUpdatedAt` is 2015-12-21 and nothing newer is published
openly: the portal's only other tree asset (`963c-68k8`) is a map view of
these rows, and no ArcGIS layer for the city turns up.  It is a snapshot, the
way Falls Church's is; the probe is what lets a republish land.

West Hollywood is its own city, an enclave inside Los Angeles, and LA's
inventory (`vt5t-mscf`, StreetsLA) stops at the city line: of LA's rows in
West Hollywood's envelope, under 1% sit within 30 m of a West Hollywood tree,
all on the boundary streets.  Those few overlaps are resolved by the shared
cluster merge like any other.

Fields:

* **id** -- the dataset has none.  `tree` is an ordinal within an address
  (`1`, `2`, ... at `315 HUNTLEY DR`), and address + `sidetype` + `tree`
  still repeats four times.  The Socrata row identifier `:id` is the only
  per-row key; it is stable across a republish of the same rows, and this
  dataset has not been republished since 2015.  Prefixed `weh-`.
* **species** -- `botanicalname` is a binomial with an optional quoted
  cultivar (`Ficus microcarpa 'Nitida'`); the schema moves the cultivar out.
  `commonname` is not mapped.  Every value carries a leading space.
* **dbh** -- a band in inches (`0-6`, `07-12`, ... `31+`; `---` for a vacant
  site), read as its midpoint, the open-topped band as its lower bound plus
  half the common width, as Denver's six-inch buckets are.
* **position** -- `location_1`, a Socrata location column with per-tree
  points (9,022 distinct among 9,105 rows over 4,046 addresses, so not an
  address geocode).  84 rows (73 of them trees) carry `0.0, 0.0` for "not
  located" and are nulled here; `validate_coordinates` drops them, leaving
  8,755 trees.
* `height` (a band in feet), `district` (maintenance zone) and `sidetype`
  (front/side/park/median/lot/rear) are not mapped.

`Vacant site` (248) and `Stump` (29) are dropped by the shared rule;
`WCA worked on private tree` (2, a contractor's note in the species column)
is a tree with no species.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    emit,
    enforce_tree_schema,
    is_not_a_tree,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)
from shared.platforms.socrata import SocrataDataset, iter_rows, point_lon_lat

DATASET = SocrataDataset("data.weho.org", "qqwt-wx9z")
SELECT = ":id,botanicalname,dbh,location_1"

# DBH band (inches) -> midpoint.  `---` is the vacant-site marker and maps to
# nothing; any other unseen label counts against PARSE_FAILURE_LIMIT.
DBH_BAND_INCHES: dict[str, float] = {
    "0-6": 3.0,
    "07-12": 9.0,
    "13-18": 15.0,
    "19-24": 21.0,
    "25-30": 27.0,
    "31+": 33.0,
}
NO_DBH = {"", "---"}
PARSE_FAILURE_LIMIT = 0.01

# Values in `botanicalname` that are notes, not names.
NON_SPECIES_VALUES = frozenset({"wca worked on private tree"})

# The portal's own typos: one Leyland cypress is `Cupressocyparis x 19leylandii`,
# and the pink trumpet tree's epithet is masculine (`heterophyllus`, POWO).
SPECIES_FIXES = {
    "Cupressocyparis x 19leylandii": "Cupressocyparis x leylandii",
    "Handroanthus heterophylla": "Handroanthus heterophyllus",
}


def clean(value) -> str:
    return (value or "").strip()


def parse_species(value) -> str | None:
    text = clean(value)
    if not text or text.lower() in NON_SPECIES_VALUES:
        return None
    return normalize_species(SPECIES_FIXES.get(text, text))


def is_tree(record: dict) -> bool:
    return not is_not_a_tree(clean(record.get("botanicalname")))


def transform(rows: list[dict], failures: list[str]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for row in rows:
        row_id = clean(row.get(":id"))
        tree_id.append(f"weh-{row_id}" if row_id else None)
        species.append(parse_species(row.get("botanicalname")))
        lon, lat = point_lon_lat(row.get("location_1"))
        if lon == 0.0 and lat == 0.0:
            lon, lat = None, None
        latitude.append(lat)
        longitude.append(lon)
        band = clean(row.get("dbh"))
        if band in NO_DBH:
            dbh.append(None)
        elif band in DBH_BAND_INCHES:
            dbh.append(DBH_BAND_INCHES[band])
        else:
            failures.append(band)
            dbh.append(None)

    n = len(tree_id)
    plant_date: list[date | None] = [None] * n
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["USWHO"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "plant_date": pa.array(plant_date, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


if __name__ == "__main__":
    failures: list[str] = []
    table = stream_to_table(
        iter_rows(DATASET, select=SELECT),
        lambda rows: transform(rows, failures),
        keep=is_tree,
        label="West Hollywood City Tree Inventory",
    )
    if failures:
        print(
            f"West Hollywood ingest: {len(failures)} unreadable dbh band(s): "
            f"{sorted(set(failures))[:10]}",
            file=sys.stderr,
        )
        if len(failures) > PARSE_FAILURE_LIMIT * table.num_rows:
            raise RuntimeError(
                f"West Hollywood dbh: {len(failures)} of {table.num_rows} bands "
                "unreadable; the portal's band labels have changed"
            )
    # A row without a Socrata id cannot be addressed; there are none today.
    missing = table.column("tree_id").null_count
    if missing:
        import pyarrow.compute as pc

        print(f"West Hollywood ingest: dropped {missing} row(s) without an id", file=sys.stderr)
        table = table.filter(pc.is_valid(table.column("tree_id")))
    table = validate_coordinates(table, city="West Hollywood", city_code="USWHO")
    table = enforce_tree_schema(table, city="West Hollywood", data_source="WEST_HOLLYWOOD")
    emit(table)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Tokyo's metropolitan-road street trees, from the Tokyo Open Data Catalog.

Source: "都道の街路樹" (street trees on metropolitan roads), published by the
Tokyo Metropolitan Government Bureau of Construction (東京都建設局) on
`catalog.data.metro.tokyo.lg.jp` under CC BY 4.0.  **One package, two CSV
resources**, which this script reads and unions:

| resource   | rows    | encoding  | surveyed  | covers                     |
|------------|--------:|-----------|-----------|----------------------------|
| 23 wards   | 144,183 | Shift-JIS | FY2020-23 | the 23 special wards       |
| Tama       |  83,875 | UTF-8     | FY2023-24 | 26 Tama municipalities     |

They are one inventory split by survey campaign, not two datasets, so they are
one `data_source` and one city.  Nothing else about them agrees: the 23-ward
file is Shift-JIS with Japanese column headers, the Tama file is UTF-8 with
romanised ones, and the row shapes differ by two columns.  `_ckan_shared.
read_csv_rows` detects the encoding per resource, which is why the difference
costs a column map here rather than two code paths.

**Scope, stated plainly because the city name over-promises it.** These are the
trees on 都道 -- roads the *metropolis* maintains.  Each ward and each Tama city
also maintains its own street trees on its own roads, and those are published
separately where they are published at all (Suginami's CSV link is dead;
Adachi's register covers 13 routes).  So this is a large sample of Tokyo's
street trees rather than Tokyo's street-tree inventory, and the same is true of
the 23-ward half and the Tama half alike.

**The source publishes no tree id, and this file synthesises one from the
coordinate.**  That is against the standing rule in `EXTENDING.md` and it is the
second deliberate exception, after Longueuil; `calon/longueuil_tree_info.py`
carries the argument in full and it applies here unchanged.  In short: neither
file has an id column (`整理番号` is the *route* number -- 316 is a road, and
5,702 rows share one), the portal publishes no other form of this data, and
97,475 mapped trees beat zero there as 228,051 do here.  The id is the rounded
coordinate and nothing else, because position is the most stable thing the
source has: `幹周` is re-measured every survey campaign, which is what a survey
exists to do, so folding an attribute into the key would churn the id of every
re-measured tree.

Two details differ from Longueuil and are worth knowing:

**7 decimal places is what these files publish**, so it is what the key rounds
to.  111,844 of the 23-ward rows carry 7 dp and none carries more; the Tama file
has six rows at 8 dp.  Rounding to 7 is ~1 cm, far below any real positional
correction, and it collides only where the source has genuinely stacked trees.

**Stacked coordinates are dropped, and there are far fewer here than in
Longueuil.**  2,079 of the 23-ward rows share a coordinate with another row
(1.4%, worst case 4 trees on one point) and the Tama file has *none at all* --
83,875 distinct coordinates over 83,875 rows.  Longueuil's 1.9% included one
point carrying 58 trees, which is a block centroid; nothing here looks like
that, so these are more likely a surveyor recording two trunks of one tree.
Either way the id cannot distinguish them and publishing an arbitrary one of a
pair would put a tree at a place no survey put it.

**Seven Tama rows have the latitude in the longitude column.**  `35.72888356,
35.728884` -- the same value to a different precision in both fields, so the
longitude is not recoverable from anything in the row.  They are dropped with a
logged count rather than left for `validate_coordinates` to bounds-filter,
because a bounds filter reports "outside CITY_BOUNDS", which is a different and
much less alarming fact than "this file has a column-order bug".

**Species is a Japanese vernacular name and nothing else** -- `イチョウ`,
`ケヤキ`, 446 distinct values.  `_japanese_species.species_from_japanese_name`
resolves 99.98% of the trees to an accepted binomial or a genus; read its
docstring before touching a mapping, because the table is curated and the two
obvious automatic sources for it are both wrong in ways that do not announce
themselves.

**`幹周` is the trunk circumference in centimetres, not a diameter.**  Both
files publish it (`幹周(cm）` -- note the full-width closing parenthesis, which
is why the column is addressed by an exact string here) and the conversion is
`circumference / pi` to get a diameter, then `/ 2.54` to get inches.  17,046
rows record `0`, which is an unmeasured tree rather than one of no width, and
two rows record 840 cm and 1,427 cm against a 99.99th percentile of 350 -- a
4.5 m trunk on a metropolitan verge, which is a decimal error rather than a
tree.  Both ends go to null.

**`行政区` is the ward, and it is published on the 23-ward file only.**  It maps
onto the shared `borough` column rather than a new `ward` one: London declares
`borough` for the same concept, `community_tree_info.py` and the OSM staging
path already emit it for every city, and a second column for "the
administrative subdivision this tree sits in" would be a parallel copy of one
that exists.  The Tama file publishes no municipality at all -- only a route
name, and its routes cross municipal boundaries -- so those rows carry null.

The other columns are read and dropped on purpose: `樹高` (height) and `枝張`
(crown spread) have no canonical column, and `区分` is 高木/中木, a size class
rather than a fact about the taxon.
"""

import math
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ckan_shared import CkanResource, read_csv_rows
from _ingest_shared import (
    emit,
    enforce_tree_schema,
    validate_coordinates,
)
from _japanese_species import species_from_japanese_name

# Both resources of package `t000014d2000000029`, "都道の街路樹".
WARDS = CkanResource("catalog.data.metro.tokyo.lg.jp", "8bdb63d0-911f-4e88-845f-14f6cab691a4", timeout=180)
TAMA = CkanResource("catalog.data.metro.tokyo.lg.jp", "033acc60-dd24-402a-9f98-67e7fbdd1ec8", timeout=180)

# The 23-ward file names its columns in Japanese and the Tama file in romaji.
# `幹周(cm）` closes with a full-width parenthesis (U+FF09) where it opens with
# an ASCII one; it is written out here rather than normalised because an exact
# key is the thing that fails loudly if the portal ever tidies it.
WARD_COLUMNS = {"species": "樹種", "circumference": "幹周(cm）", "borough": "行政区",
                "longitude": "経度", "latitude": "緯度"}
TAMA_COLUMNS = {"species": "name", "circumference": "perimeter", "borough": None,
                "longitude": "longitude", "latitude": "latitude"}

# Tokyo's mainland spans 139.0 to 139.9 E; the Izu and Ogasawara islands are
# part of the metropolis and carry none of these trees.  A value outside this
# is the swapped-column bug, not an island.
LON_RANGE = (138.0, 141.0)
LAT_RANGE = (34.0, 37.0)

# The published coordinate precision (see the module docstring).
COORD_DP = 7

# Circumference in centimetres.  0 is the unmeasured sentinel -- 17,046 rows
# carry it -- and the top end is two rows against a 99.99th percentile of 350.
MIN_CIRCUMFERENCE_CM = 1.0
MAX_CIRCUMFERENCE_CM = 600.0


def parse_dbh(value) -> float | None:
    """Trunk circumference in cm to diameter at breast height in inches."""
    try:
        circumference = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if circumference < MIN_CIRCUMFERENCE_CM or circumference > MAX_CIRCUMFERENCE_CM:
        return None
    return circumference / math.pi / 2.54


def parse_point(row: dict, columns: dict) -> tuple[float, float] | None:
    """`(lon, lat)` for a row, or None when either is unusable.

    Rejects the seven Tama rows whose longitude column holds the latitude:
    both fields parse as floats, so only a range check separates them from a
    real coordinate.
    """
    try:
        lon = float(str(row.get(columns["longitude"]) or "").strip())
        lat = float(str(row.get(columns["latitude"]) or "").strip())
    except (TypeError, ValueError):
        return None
    if not (LON_RANGE[0] < lon < LON_RANGE[1]):
        return None
    if not (LAT_RANGE[0] < lat < LAT_RANGE[1]):
        return None
    return lon, lat


def coord_key(point: tuple[float, float]) -> tuple[float, float]:
    """The rounded `(lon, lat)` a tree's id is built from."""
    return round(point[0], COORD_DP), round(point[1], COORD_DP)


def read_resource(resource: CkanResource, columns: dict, label: str) -> list[dict]:
    """One CSV resource as `{point, key, species, borough, circumference}` dicts.

    Rows with no usable coordinate are dropped here, with a count, because the
    id is built from the coordinate: a row without one cannot be keyed at all.
    """
    rows = read_csv_rows(resource)
    kept = []
    for row in rows:
        point = parse_point(row, columns)
        if point is None:
            continue
        borough_column = columns["borough"]
        kept.append(
            {
                "key": coord_key(point),
                "lon": point[0],
                "lat": point[1],
                "species": (row.get(columns["species"]) or "").strip() or None,
                "borough": ((row.get(borough_column) or "").strip() or None)
                if borough_column
                else None,
                "circumference": row.get(columns["circumference"]),
            }
        )
    unusable = len(rows) - len(kept)
    print(
        f"{label}: {len(rows)} row(s); dropped {unusable} with no usable "
        f"coordinate, leaving {len(kept)}",
        file=sys.stderr,
    )
    return kept


def transform(records: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    borough: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float] = []
    longitude: list[float] = []
    dbh: list[float | None] = []

    for rec in records:
        lon, lat = rec["key"]
        tree_id.append(f"tyo-{lon:.7f}-{lat:.7f}")
        species.append(species_from_japanese_name(rec["species"]))
        borough.append(rec["borough"])
        # The survey records no planting date.  Still a typed date32 column: an
        # untyped pa.null() lands in the parquet as INT32 and breaks year().
        plant_date.append(None)
        latitude.append(rec["lat"])
        longitude.append(rec["lon"])
        dbh.append(parse_dbh(rec["circumference"]))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["JPTYO"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "borough": pa.array(borough, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


def main() -> None:
    records = read_resource(WARDS, WARD_COLUMNS, "Tokyo OpenData (23 wards)")
    records += read_resource(TAMA, TAMA_COLUMNS, "Tokyo OpenData (Tama)")

    # A coordinate carrying more than one row cannot be keyed; see the module
    # docstring.  Counted across both files together, since the two halves are
    # one inventory and a route can be surveyed from either side of a boundary.
    counts = Counter(rec["key"] for rec in records)
    kept = [rec for rec in records if counts[rec["key"]] == 1]
    stacked_points = sum(1 for n in counts.values() if n > 1)
    stacked_rows = sum(n for n in counts.values() if n > 1)
    print(
        f"Tokyo OpenData: {len(records)} row(s); dropped {stacked_rows} at "
        f"{stacked_points} shared coordinate(s), leaving {len(kept)}",
        file=sys.stderr,
    )

    table = transform(kept)
    table = validate_coordinates(table, city="Tokyo", city_code="JPTYO")
    table = enforce_tree_schema(table, city="Tokyo", data_source="TOKYO_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()

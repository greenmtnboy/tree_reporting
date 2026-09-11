#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Taipei's street and park trees, from the Parks and Street Lights Office.

Source: 「臺北市行道樹及公園樹木分布圖」 (street trees and park trees), published
by the Taipei City Government Parks and Street Lights Office (臺北市政府工務局
公園路燈工程管理處) on `data.taipei` under the Taiwan Open Government Data
License v1 (CC BY 4.0-compatible).  **One dataset, two CSV files**, which this
script reads and unions:

| file               | rows   | id prefix | covers                  |
|--------------------|-------:|-----------|-------------------------|
| TaipeiTree.csv     | 90,976 | BT/SL/... | street trees, 12 districts |
| TaipeiParkTree.csv | 72,011 | SL/WH/... | park trees, 12 districts   |

Both are UTF-8 with a BOM and English column headers.  A third file in the
dataset (`TaipeiTreeHole.csv`) is the tree *pits* -- planting sites with a
shape and an area and no tree -- and is not read.

**`TreeID` is a real per-tree key.**  Checked over both whole files rather
than a first page, as `EXTENDING.md` asks: 90,976 distinct in 90,976 street
rows, 72,011 in 72,011 park rows, none null, and the two sets are disjoint.

**Species is a Traditional-Chinese common name and nothing else** -- `榕樹`,
`茄苳`, `樟樹`, 471 distinct values across the two files, no binomial
anywhere.  `_chinese_species.species_from_chinese_name` resolves them; read
its docstring before touching a mapping, because the table is curated and
checked against POWO value by value, and Taiwan's usage of a name is not
always the mainland's (`楓香` is *Liquidambar formosana*, `青楓` is *Acer
serrulatum*).

**Coordinates are TWD97 / TM2 zone 121 (EPSG:3826)**, the national grid, in
metres.  `_ingest_shared.twd97_to_wgs84` inverts the projection; the datum is
ITRF94, which is WGS84 to well under a metre, so no shift is applied.  The
files publish nothing else for position.

**`Diameter` is a diameter in centimetres** -- the survey's 胸徑, measured at
1.3 m -- and the median is 24 cm, which is a street tree.  Zero is the
unmeasured sentinel (53 street rows, 9,266 park rows) and goes to null; the
top end has a park row at 2,637 cm and a street row at 245 against 99th
percentiles of 75 and 91, and `enforce_tree_schema`'s cap takes the ones that
cannot be a trunk.  `TreeHeight` is read and dropped (no canonical column; it
also carries a 2,023 m tree).

**`SurveyDate` is a survey date, not a planting date**, and is not published
as one: the model would read a 2022 survey of a fifty-year-old banyan as a
two-year-old tree.  `plant_date` is null for the whole city.

`Dist` is the 區, Taipei's district, and rides the shared `borough` column
the way London's borough and Tokyo's ward do.
"""

import csv
import io
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _chinese_species import species_from_chinese_name
from _ingest_shared import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    get_with_retry,
    twd97_to_wgs84,
    validate_coordinates,
)

# The dataset's three resources are static blobs; these two are the trees.
# `taipei_update_time.py` probes the same two URLs.
STREET_TREES = "https://tppkl.blob.core.windows.net/blobfs/TaipeiTree.csv"
PARK_TREES = "https://tppkl.blob.core.windows.net/blobfs/TaipeiParkTree.csv"

# Diameter in centimetres.  0 is the unmeasured sentinel; the upper bound is
# left to `enforce_tree_schema`, whose cap is a guard against a wrong column
# rather than a unit converter.
MIN_DIAMETER_CM = 1.0


def read_csv(url: str, label: str) -> list[dict]:
    body = get_with_retry(url, timeout=300).content
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    print(f"{label}: {len(rows)} row(s)", file=sys.stderr)
    return rows


def parse_dbh(value) -> float | None:
    try:
        diameter = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if diameter < MIN_DIAMETER_CM:
        return None
    return cm_to_inches(diameter)


def parse_point(row: dict) -> tuple[float, float] | None:
    """`(lat, lon)` from the TWD97 easting/northing, or None."""
    try:
        x = float(str(row.get("TWD97X") or "").strip())
        y = float(str(row.get("TWD97Y") or "").strip())
    except (TypeError, ValueError):
        return None
    # Taipei's grid coordinates sit in a narrow band; anything else is a
    # column-order or unit slip, not a tree somewhere unexpected.
    if not (280000 < x < 330000 and 2740000 < y < 2800000):
        return None
    return twd97_to_wgs84(x, y)


def transform(rows: list[dict], label: str) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    borough: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float] = []
    longitude: list[float] = []
    dbh: list[float | None] = []
    unusable = 0

    for row in rows:
        raw_id = (row.get("TreeID") or "").strip()
        point = parse_point(row)
        if not raw_id or point is None:
            unusable += 1
            continue
        tree_id.append(f"tpe-{raw_id}")
        species.append(species_from_chinese_name(row.get("TreeType")))
        borough.append((row.get("Dist") or "").strip() or None)
        # A survey date is not a planting date; see the module docstring.
        plant_date.append(None)
        latitude.append(point[0])
        longitude.append(point[1])
        dbh.append(parse_dbh(row.get("Diameter")))

    print(
        f"{label}: dropped {unusable} row(s) with no id or no usable grid "
        f"coordinate, leaving {len(tree_id)}",
        file=sys.stderr,
    )
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["TWTPE"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "borough": pa.array(borough, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


def main() -> None:
    street = transform(read_csv(STREET_TREES, "Taipei OpenData (street trees)"), "Taipei street trees")
    park = transform(read_csv(PARK_TREES, "Taipei OpenData (park trees)"), "Taipei park trees")
    table = pa.concat_tables([street, park])
    table = validate_coordinates(table, city="Taipei", city_code="TWTPE")
    table = enforce_tree_schema(table, city="Taipei", data_source="TAIPEI_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""San Diego, CA's public trees: the Transportation Department's tree inventory.

**Source.**  `Trees (Street Trees)`, layer 20 of `DSD/Environment/MapServer`
on webmaps.sandiego.gov, the City of San Diego's own ArcGIS Server: "point
features for all public trees within the City of San Diego ... compiled from
the City's records", "created and is maintained by the City of San Diego's
Transportation Department" (its Urban Forestry program; the City's
interactive street tree map reads the same inventory).  It is the only tree
layer on the City's server, and data.sandiego.gov (whose datasets are PDDL)
publishes no tree inventory.  **No licence is stated**: the layer's item
info carries only the SanGIS/City warranty disclaimer ("provided on an 'AS
IS' ... basis"), so this source is displayed with attribution only, not
licensed for reuse.

**Rows.**  365,656 rows on 2026-11-18.  `MAPNG_STAT_CD` is the asset's
status in the City's SAP asset register: `AB` (275,614) is a standing tree,
`RM` (90,042) a removed one -- the removed rows carry no species, no
inspection and no diameter.  Only `AB` is read.  Of those, 100 are `Stump`
in `COMMON_NAME` and are dropped (`is_not_a_tree`).  `CONDITION` = `DEAD`
(1,626 rows; the domain is GOOD/FAIR/POOR/DEAD, and about 47k rows carry
undocumented codes `1`-`4`, read as nothing) is a standing dead tree and
publishes as the `Dead` sentinel.

**Two generations of record.**  174,740 standing rows were re-inventoried in
the City's recent contract inventory: they have an `INSPCTDATE` (2020s),
a scientific name in `SPECIES_NAME`, and a diameter recorded *in 6-inch
classes* -- every one of them is a multiple of 6 (6: 54k, 12: 61k, 18: 37k,
...; no other value occurs).  The other ~101k are the older inventory: no
inspection, no `SPECIES_NAME`, only a `COMMON_NAME`, and a diameter to the
inch (5: 2,133, 6: 2,370, 7: 1,627 -- no banding).  So:

* `species` is `SPECIES_NAME` where there is one (always a binomial, the
  sanitiser handles `spp.`/`species`, quoted cultivars and the hybrid mark);
  otherwise the common name through `shared.species.english` -- a name the
  table does not hold publishes as `Unknown` (see the report for the entries
  San Diego needs).  The layer's own common-name/scientific-name pairs were
  the drafting aid for those entries.
* `diameter_at_breast_height`: an inspected row's value is the top of its
  class (`6` is "0-6", `12` is "7-12": the smallest class is 6, where most
  young street trees are), so it publishes as the class midpoint, value
  minus 2.5 in; an uninspected row's value is published as recorded.  The
  schema nulls 0 and anything over 200.

**`INST_YR` is not a planting date** for most rows.  Of its ~207k values on
standing trees, 98% fall in 2000-2002 (the year the records were loaded into
the City's asset system: 16% of those "2000-2002 plantings" have a trunk of
24 in or more), and the oldest is 2000.  Only the dates from
`PLANT_DATE_FROM` on (a few hundred rows, 2018-2026, the newest with no
diameter yet, as a just-planted tree would have) are published; earlier ones
are nulled.

**Ids are the layer's `GlobalID`** (`sd-{guid}`), the geodatabase's
system-maintained per-feature id: unique and non-null by construction, and
`enforce_tree_schema` checks it.  The City's own asset ids were weighed:
`GISID` (`TR006355`) and `EQPID` are carried on every row too, but a
`groupBy ... having COUNT > 1` statistics query over 365k rows times out on
this server, so their uniqueness cannot be shown, and GlobalID is preferred
anyway.  A row without one is dropped and counted.

`HEIGHT_FT` is a 10-ft class and is not published (heights come from
LIDAR).  Coordinates are the point geometry reprojected server-side from
State Plane (EPSG:2230) to WGS84; the layer's `LATITUDE`/`LONGITUDE`
attribute columns are almost all null.
"""

import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_ms_to_date,
    esri_point,
    iter_features,
)
from shared.ingest import (
    DEAD_SPECIES,
    emit,
    enforce_tree_schema,
    is_not_a_tree,
    stream_to_table,
    validate_coordinates,
)
from shared.species.english import species_from_common_name

LAYER = FeatureLayer(
    "https://webmaps.sandiego.gov/arcgis/rest/services/DSD/Environment/MapServer/20",
    timeout=180,
)
SOURCE_URL = LAYER.base

STANDING = "MAPNG_STAT_CD = 'AB'"
OUT_FIELDS = (
    "OBJECTID,GlobalID,MAPNG_STAT_CD,SPECIES_NAME,COMMON_NAME,CONDITION,"
    "DBH_IN,INSPCTDATE,INST_YR"
)

# `INST_YR` before this is the 2000-2002 load of the asset register, not a
# planting (see the docstring).
PLANT_DATE_FROM = date(2006, 1, 1)

# An inspected row's diameter is the top of a 6-inch class.
DBH_CLASS_WIDTH = 6
DBH_CLASS_MIDPOINT_OFFSET = 2.5

# Refuse to publish when an inspected row's diameter is off the class grid on
# more than this share of rows: the recording rule changed.
MAX_OFF_GRID_SHARE = 0.01

DROPPED: Counter[str] = Counter()
STATS: Counter[str] = Counter()


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def species_of(rec: dict) -> str | None:
    if clean(rec.get("CONDITION")) == "DEAD":
        STATS["dead"] += 1
        return DEAD_SPECIES
    scientific = clean(rec.get("SPECIES_NAME"))
    if scientific is not None:
        STATS["species from SPECIES_NAME"] += 1
        return scientific
    resolved = species_from_common_name(clean(rec.get("COMMON_NAME")))
    STATS["species from COMMON_NAME" if resolved else "common name unresolved"] += 1
    return resolved


def diameter_of(rec: dict) -> float | None:
    value = rec.get("DBH_IN")
    if value is None:
        return None
    number = float(value)
    if rec.get("INSPCTDATE") is None:
        return number
    STATS["inspected diameter"] += 1
    if number <= 0:
        return None
    if number % DBH_CLASS_WIDTH:
        STATS["inspected diameter off the class grid"] += 1
        return number
    return number - DBH_CLASS_MIDPOINT_OFFSET


def plant_date_of(rec: dict) -> date | None:
    planted = esri_ms_to_date(rec.get("INST_YR"))
    if planted is None:
        return None
    if planted < PLANT_DATE_FROM:
        STATS["INST_YR before the cutoff, nulled"] += 1
        return None
    return planted


def keep(feature: dict) -> bool:
    rec = feature.get("attributes") or {}
    if rec.get("MAPNG_STAT_CD") != "AB":
        raise RuntimeError(f"San Diego row outside the standing filter: {rec!r}"[:300])
    if not str(rec.get("GlobalID") or "").strip("{} "):
        DROPPED["no GlobalID"] += 1
        return False
    if is_not_a_tree(clean(rec.get("COMMON_NAME"))) or is_not_a_tree(clean(rec.get("SPECIES_NAME"))):
        DROPPED[f"not a tree ({clean(rec.get('COMMON_NAME'))})"] += 1
        return False
    return True


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        guid = str(rec["GlobalID"]).strip().strip("{}").lower()
        lat, lon = esri_point(feature.get("geometry"))
        tree_id.append(f"sd-{guid}")
        species.append(species_of(rec))
        plant_date.append(plant_date_of(rec))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(diameter_of(rec))

    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["USSAN"] * len(tree_id), type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "plant_date": pa.array(plant_date, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


def iter_row_chunks():
    yield from iter_features(
        LAYER,
        out_fields=OUT_FIELDS,
        where=STANDING,
        return_geometry=True,
        out_sr=4326,
    )


def check_diameter_grid() -> None:
    inspected = STATS["inspected diameter"]
    off = STATS["inspected diameter off the class grid"]
    if inspected and off / inspected > MAX_OFF_GRID_SHARE:
        raise RuntimeError(
            f"San Diego: {off:,} of {inspected:,} inspected diameters are not "
            f"multiples of {DBH_CLASS_WIDTH} in; the class rule no longer holds"
        )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, keep=keep, label="San Diego OpenData")
    check_diameter_grid()
    print(
        f"San Diego: dropped {dict(DROPPED)}; {dict(STATS)}",
        file=sys.stderr,
    )
    table = validate_coordinates(table, city="San Diego", city_code="USSAN")
    table = enforce_tree_schema(
        table, city="San Diego", data_source="SANDIEGO_OPENDATA"
    )
    emit(table)

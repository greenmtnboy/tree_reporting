#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Oxnard, California's City tree inventory.

Licence: none stated; displayed with attribution only, not licensed for
reuse.  The item's `licenseInfo` is a disclaimer, not a licence: "This data
is compiled by the City of Oxnard GIS Division, which is developed and
operated solely for the convenience of the City. The City does not make any
representations or warranties regarding the use, accuracy, reliability,
and/or completeness of the data ..."
(https://www.arcgis.com/home/item.html?id=a5aa2d1dfd344ef79d61507d33cdbc02).
It does not forbid redistribution.

Source: "Trees" (ArcGIS item a5aa2d1dfd344ef79d61507d33cdbc02, owner
`ryan.bosley@oxnard.org`, City of Oxnard GIS), the City's own map service
`maps.oxnard.org/arcgis/rest/services/ParksLayers/MapServer/1`: 63,300 rows
on 2026-11-23, loaded from the contractor's (West Coast Arborists) June 2022
database download (`Source`) and edited by the City since; the newest
`DateModified` is 2023-12-18.  There is no FeatureServer twin (the
`featureserver` extension is not enabled); the MapServer pages by offset
(`maxRecordCount` 1000, `supportsPagination`).  Stored in EPSG:2229 (State
Plane CA V, feet) and read reprojected to WGS84 (`outSR=4326`).

**Which rows are trees.**  `BOTANICALN` records the empty sites as values:
`Vacant site` (11,074 in Nov 2026, condition `Missing`, recommendation
`Plant`), `Unsuitable site` (177, `Do Not Replant`) and `Stump` (151,
`Removal-Stump`); all are dropped with a logged count, as are the 12 rows
whose only name is the common name `UNSUITABLE SITE`.  `TREECONDIT` is a
coded-value domain (Good, Fair, Poor, Dead, Missing, N/A, Needs Inspection):
a named tree in condition `Missing` (394) is a tree the inventory could not
find and is dropped (Lansing's rule); `Dead` stays (a dead tree is a tree).
An unseen condition code refuses the build.  Private, County and district
trees (`OwnedBy`) are trees and are kept.

**The id is the GlobalID** (`oxnard-{guid}`), non-null and distinct on all
63,300 rows; a repeated GlobalID refuses the build.  `INVENTORYI` (the
contractor's inventory number) repeats on three rows, and OBJECTIDs
(187,083..265,055 with gaps) are the geodatabase's row numbers.

**Species is `BOTANICALN`**, a Latin name with the cultivar quoted
(`Ficus microcarpa 'Nitida'`), which the shared hygiene splits.  `Other
tree`, `Dead Tree` and `Unknown` name no taxon and publish Unknown.  A row
with no botanical name is read from `COMMONNAME` through the shared English
table (`FERN PINE`, 9 rows); the 193 with neither are trees with no species
(added by the special districts) and publish Unknown.  Where a Latin name is
present it wins.

**DBH is in inches.**  `ACTUALDBH` (a text field, blank on ~74% of rows) is
the measured diameter and wins where it reads as a positive number; its
values sit inside the `DBH` class on nearly every row, which is how the unit
was checked.  Otherwise `DBH`, a 6-inch class (`0-6`, `07-12`, ... `25-30`,
`31+`), is read as the class midpoint (Ballarat's rule); the open top class
takes 34 in (Vienna's rule, half a band past its floor).  `---` and `0` are
no measurement.  An unseen class or a non-numeric `ACTUALDBH` counts as
unreadable, and more than 1% unreadable refuses the build.  `HEIGHT` is a
height class in feet and is not published (height comes from LIDAR); the
layer has no crown spread and no planting date.
"""

import re
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.species.english import species_from_common_name
from shared.ingest import (
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://maps.oxnard.org/arcgis/rest/services/ParksLayers/MapServer/1",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,GlobalID,BOTANICALN,COMMONNAME,DBH,ACTUALDBH,TREECONDIT"
ID_PREFIX = "oxnard"

# Refuse to publish when more than this share of rows cannot be read.
MAX_UNPARSED_SHARE = 0.01

# `BOTANICALN` values (lower-cased) that are sites, not trees.
NOT_TREES = frozenset({"vacant site", "unsuitable site", "stump"})
# Common names that are sites, read only where `BOTANICALN` is empty.
NOT_TREE_COMMON = frozenset({"vacant site", "unsuitable site", "stump"})
# `BOTANICALN` values that name no taxon: the tree publishes Unknown.
NO_TAXON = frozenset({"other tree", "dead tree", "unknown"})

# `TREECONDIT` coded values (the `TreeCondition` domain).
KEPT_CONDITIONS = frozenset({"Good", "Fair", "Poor", "Dead", "N/A", "Needs Inspection", None})
DROPPED_CONDITIONS = frozenset({"Missing"})

# `DBH` class -> inches.
DBH_CLASS = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")
OPEN_TOP = {"31+": 34.0}
NO_DBH = frozenset({"---", "0"})


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("‘", "'").replace("’", "'").split())
    return text or None


class Counts:
    no_geometry = 0
    no_id = 0
    sites: Counter = Counter()
    missing = 0
    unseen_condition: Counter = Counter()
    rows = 0
    unparsed_dbh: Counter = Counter()
    measured = 0
    banded = 0
    from_common = 0
    unresolved_common: Counter = Counter()


COUNTS = Counts()
SEEN_IDS: set[str] = set()


def keep(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    botanical = clean(attrs.get("BOTANICALN"))
    common = clean(attrs.get("COMMONNAME"))
    if botanical and botanical.lower() in NOT_TREES:
        COUNTS.sites[botanical] += 1
        return False
    if not botanical and common and common.lower() in NOT_TREE_COMMON:
        COUNTS.sites[f"common name {common}"] += 1
        return False
    condition = attrs.get("TREECONDIT")
    if condition in DROPPED_CONDITIONS:
        COUNTS.missing += 1
        return False
    if condition not in KEPT_CONDITIONS:
        raise RuntimeError(
            f"Oxnard ingest: unseen TREECONDIT code {condition!r}; "
            "the TreeCondition domain has changed"
        )
    guid = str(attrs.get("GlobalID") or "").strip().strip("{}").lower()
    if not guid:
        COUNTS.no_id += 1
        return False
    if guid in SEEN_IDS:
        raise RuntimeError(f"Oxnard ingest: GlobalID {guid} is repeated")
    SEEN_IDS.add(guid)
    lat, lon = esri_point(feature.get("geometry"))
    if lat is None or lon is None:
        COUNTS.no_geometry += 1
        return False
    return True


def species_of(attrs: dict) -> str | None:
    """The species value of a kept row, before the shared hygiene.

    Examples:
        "Ficus microcarpa 'Nitida'"         -> "Ficus microcarpa 'Nitida'"
        "Other tree"                        -> None
        None, common "FERN PINE"            -> the shared English table's answer
    """
    botanical = clean(attrs.get("BOTANICALN"))
    if botanical:
        return None if botanical.lower() in NO_TAXON else botanical
    common = clean(attrs.get("COMMONNAME"))
    if common is None:
        return None
    species = species_from_common_name(common)
    if species:
        COUNTS.from_common += 1
        return species
    COUNTS.unresolved_common[common] += 1
    return None


def dbh_inches(attrs: dict) -> float | None:
    """Measured `ACTUALDBH` if positive, else the `DBH` class midpoint.

    Examples:
        ACTUALDBH "12", DBH "07-12" -> 12.0
        ACTUALDBH " ",  DBH "13-18" -> 15.5
        ACTUALDBH None, DBH "31+"   -> 34.0
        ACTUALDBH "0",  DBH "---"   -> None
    """
    actual = clean(attrs.get("ACTUALDBH"))
    if actual is not None:
        try:
            value = float(actual)
        except ValueError:
            COUNTS.unparsed_dbh[f"ACTUALDBH {actual}"] += 1
            value = None
        if value is not None and value > 0:
            COUNTS.measured += 1
            return value
    band = clean(attrs.get("DBH"))
    if band is None or band in NO_DBH:
        return None
    if band in OPEN_TOP:
        COUNTS.banded += 1
        return OPEN_TOP[band]
    match = DBH_CLASS.match(band)
    if match is None:
        COUNTS.unparsed_dbh[f"DBH {band}"] += 1
        return None
    COUNTS.banded += 1
    return (int(match.group(1)) + int(match.group(2))) / 2


def iter_chunks():
    yield from iter_features(
        LAYER,
        out_fields=OUT_FIELDS,
        return_geometry=True,
        out_sr=4326,
        order_by="OBJECTID",
    )


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    latitude: list[float] = []
    longitude: list[float] = []
    dbh: list[float | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        guid = str(attrs["GlobalID"]).strip().strip("{}").lower()
        COUNTS.rows += 1
        tree_id.append(f"{ID_PREFIX}-{guid}")
        species.append(species_of(attrs))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(dbh_inches(attrs))

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["USOXR"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "plant_date": pa.array([None] * n, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


if __name__ == "__main__":
    table = stream_to_table(iter_chunks(), transform, keep=keep, label="Oxnard OpenData")
    print(
        f"Oxnard ingest: dropped sites {dict(COUNTS.sites)}, {COUNTS.missing} trees in "
        f"condition Missing, {COUNTS.no_id} with no GlobalID, "
        f"{COUNTS.no_geometry} with no geometry",
        file=sys.stderr,
    )
    print(
        f"Oxnard ingest: DBH measured on {COUNTS.measured:,} trees, from its class on "
        f"{COUNTS.banded:,}; {COUNTS.from_common} trees with no Latin name read from "
        f"their common name, unresolved: {COUNTS.unresolved_common.most_common(10)}",
        file=sys.stderr,
    )
    unparsed = sum(COUNTS.unparsed_dbh.values())
    if unparsed:
        print(
            f"Oxnard ingest: {unparsed} DBH values unreadable: "
            f"{COUNTS.unparsed_dbh.most_common(10)}",
            file=sys.stderr,
        )
    if COUNTS.rows and unparsed / COUNTS.rows > MAX_UNPARSED_SHARE:
        raise RuntimeError(
            f"Oxnard ingest: {unparsed} of {COUNTS.rows} DBH values unreadable; "
            "the DBH/ACTUALDBH format has changed"
        )
    table = validate_coordinates(table, city="Oxnard", city_code="USOXR")
    table = enforce_tree_schema(
        table, city="Oxnard", data_source="OXNARD_OPENDATA"
    )
    emit(table)

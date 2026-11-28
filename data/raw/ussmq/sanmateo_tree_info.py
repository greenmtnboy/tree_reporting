#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""San Mateo, California's City street and park trees, from the City's ArcGIS Online organisation.

LICENCE: no licence stated; displayed with attribution only, not licensed for
reuse.  The item's `licenseInfo`, `accessInformation` are empty and its
description is "Street Trees for Context Webscene"
(https://www.arcgis.com/home/item.html?id=67c8b57d2d91459c9f951df9de961a06);
the City publishes no open-data terms for its ArcGIS organisation.

Source: "Street_Trees" (ArcGIS item 67c8b57d2d91459c9f951df9de961a06, owner
`pworks_forsanmateo`, City of San Mateo Public Works, organisation
`g26Y0m7OCdjU0ObA` "City of San Mateo"), the service's only layer
`Street_Trees/FeatureServer/0`: 24,045 rows on 2026-11-27.  It is the City's
tree-inventory software export (the same ArborAccess-style schema as
Davenport's: `ACTIVE`, `UNIQUEID`, `SPP`, `DBH`, `MT`, ...), inventoried
2009-12 to 2019-03 (`INV_DATE`, `INSPECT_DT`) and loaded in February 2020
(`editingInfo.dataLastEditDate` 2020-02-28), not edited since.  Street trees
(`AREA = SAN MATEO`, 18,516) plus the trees of the City's parks and
facilities (Central Park, Beresford, Bayside/Joinville, ...) and 974 marked
`OWNER MAINTAINED`.  Geometry is stored in California State Plane III (feet,
EPSG 2227) and read in WGS84 (`outSR=4326`); the `LATITUDE`/`LONGITUDE`
attributes sit a constant 1.3 m off it (a different datum transformation)
and are not read.

Two other City layers were checked and are not read: `Cartegraph_View`
layer 6 "Tree" (the City's asset-management view, 23,011 rows, every row
`LASTEDITOR = DataLoader`, `LASTUPDATE` 2014-08-22: a 2014 load of this same
inventory -- `FACILITYID` is this layer's `UNIQUEID` -- missing the 1,299
trees inventoried 2016-2019 here, with diameter rounded to size classes), and
`Central_Park_Trees` (506 rows, a 2022 extract of Central Park for its
centennial; 457 of its `Remote_ID`s are rows here).

**The id is `UNIQUEID`** (`sanmateo-{UNIQUEID}`, its space written `-`:
`MJ 20091207160434` -> `sanmateo-MJ-20091207160434`), the inventory's own
record id (surveyor initials and creation stamp, or `7055_...` for later
records): non-null and unique over the whole layer (24,045 distinct), and the
key the City's Cartegraph asset system carries (as `FACILITYID`), so it
survives a move between the City's systems.  The numeric `ID` (37,086..62,655
with gaps) is unique too but Cartegraph does not keep it; the layer has no
GlobalID, and `OBJECTID` is a dense 1..24,045 row number.  A row with no
`UNIQUEID` is dropped with a logged count; a repeated one raises (the
schema's grain check).

**Which rows are trees.**  Sites and stumps are recorded in the species
column: `SPP` `Vacant site` (376), `vacant site small|medium|large`, `Stump`
(236), `No tree` (51) and `No Planting Site` (11) are dropped, and so is any
row whose maintenance need (`MT`) is `Stump Removal` (the tree is gone; 248
rows, 235 of them `Stump`).  `CONDTOTAL = Dead` and trees scheduled for
removal are still standing and stay.  `ACTIVE` is 1 on every row; another
value is dropped and counted.

**Species is `SPP`**, the botanical name (`Platanus X acerifolia`,
`Pyrus calleryana 'Aristocrat'`), read through the shared sanitiser, which
moves a quoted cultivar to `cultivar`, truncates to species rank and folds
the synonyms (`Sapium sebiferum`, `Tristania conferta`, `Rhus lancea`).  A
few cultivars are written without quotes (`Zelkova serrata Wireless`,
`Fraxinus velutina modesto`, `Robinia ambigua idahoensis`); `REWRITES` quotes
them so they reach `cultivar` rather than being truncated away, and restores
the names the export cut at its 25-character field width
(`Lyonothamnus asplenifoliu`, `Archontophoenix cunningham`).
`Other spp` is `Unknown`.  A value that does not read as a Latin name counts
as unreadable; above 1% the build is refused.  There is no common-name
column on this layer.

**DBH is `DBH`, inches** (a string of whole inches: median 10, 99th
percentile 46, max 113; the original 2009-10 survey recorded size classes
2/5/10/16/22/28/34/40/46/52/55, later records the measured inch).  0 and -1
are unmeasured and nulled by the schema; an unreadable value counts toward
the same 1% guard.  `TRUNKS` is a stem count and is not read.

No planting date is recorded (`INV_DATE` is the survey date).  `HEIGHT` and
`WIDTH` are 10-foot bands ("11 to 20", "51 +"), not a measured height or crown
spread, and are not mapped.
"""

import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features  # noqa: E402
from shared.ingest import (  # noqa: E402
    UNKNOWN_SPECIES,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

SOURCE_URL = (
    "https://services2.arcgis.com/g26Y0m7OCdjU0ObA/arcgis/rest/services/"
    "Street_Trees/FeatureServer/0"
)
LAYER = FeatureLayer(SOURCE_URL, timeout=180)
OUT_FIELDS = "OBJECTID,UNIQUEID,ACTIVE,SPP,DBH,MT"
ID_PREFIX = "sanmateo"

# `SPP` values (lower-cased) that are a site or a stump, not a tree.
NOT_TREES = frozenset({
    "vacant site",
    "vacant site small",
    "vacant site medium",
    "vacant site large",
    "stump",
    "no tree",
    "no planting site",
})
# `MT` values that say the tree is gone.
GONE_MAINTENANCE = frozenset({"stump removal"})
UNKNOWN_VALUES = frozenset({"other spp", "unknown"})

# Cultivars this inventory writes without quotes (or with a broken quote),
# rewritten so the shared sanitiser moves them to `cultivar`.  Keyed on the
# lower-cased published value.
REWRITES = {
    "zelkova serrata wireless": "Zelkova serrata 'Wireless'",
    "prunus serrulata kwanzan": "Prunus serrulata 'Kwanzan'",
    "tristania laurina elegant": "Tristania laurina 'Elegant'",
    "pyrus calleryana chanticleer": "Pyrus calleryana 'Chanticleer'",
    "platanus acerifolia yarwood": "Platanus x acerifolia 'Yarwood'",
    "fraxinus velutina modesto": "Fraxinus velutina 'Modesto'",
    "fraxinus holo moraine": "Fraxinus holotricha 'Moraine'",
    "robinia ambigua idahoensis": "Robinia x ambigua 'Idahoensis'",
    "robina idahoensis": "Robinia x ambigua 'Idahoensis'",
    "populus nigra italica": "Populus nigra 'Italica'",
    "carpinus betulus 'fastigia": "Carpinus betulus 'Fastigiata'",
    "juniperus torulosa": "Juniperus chinensis 'Torulosa'",
    # Cut at the export's field width.
    "lyonothamnus asplenifoliu": "Lyonothamnus floribundus",
    "archontophoenix cunningham": "Archontophoenix cunninghamiana",
    # An interspecific hybrid with no binomial ('Green Giant'): genus only,
    # not `Thuja standishii`, which the sanitiser would truncate it to.
    "thuja standishii x plicata": "Thuja",
    # No such species; Davidia's one species.
    "davidia laevigatum": "Davidia involucrata",
    "dogwood spp": "Cornus",
}

MAX_UNPARSED_SHARE = 0.01

# A genus (any case), then words of letters, digits, quotes, dots and
# hyphens: epithet, hybrid mark, quoted cultivar, `spp.`.
LATIN = re.compile(r"^[A-Za-z]+( [A-Za-z0-9.'\-]+)*$")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("\xa0", " ").split())
    return text or None


class Counts:
    no_id = 0
    no_geometry = 0
    dropped: Counter = Counter()
    unparsed: Counter = Counter()
    named = 0
    dbh_seen = 0
    dbh_unparsed: Counter = Counter()


COUNTS = Counts()


def keep(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    botanical = (clean(attrs.get("SPP")) or "").lower()
    maintenance = (clean(attrs.get("MT")) or "").lower()
    if botanical in NOT_TREES:
        COUNTS.dropped[botanical] += 1
        return False
    if maintenance in GONE_MAINTENANCE:
        COUNTS.dropped[f"MT {maintenance} ({botanical})"] += 1
        return False
    if attrs.get("ACTIVE") != 1:
        COUNTS.dropped[f"ACTIVE {attrs.get('ACTIVE')!r}"] += 1
        return False
    if clean(attrs.get("UNIQUEID")) is None:
        COUNTS.no_id += 1
        return False
    lat, lon = esri_point(feature.get("geometry"))
    if lat is None or lon is None:
        COUNTS.no_geometry += 1
        return False
    return True


def parse_species(raw) -> str | None:
    """The botanical name as published, for the shared sanitiser.

    Examples:
        "Platanus X acerifolia"      -> "Platanus X acerifolia"
        "Zelkova serrata Wireless"   -> "Zelkova serrata 'Wireless'"
        "Other spp"                  -> "Unknown"
        ""                           -> None
    """
    text = clean(raw)
    if text is None:
        return None
    COUNTS.named += 1
    lowered = text.lower()
    if lowered in UNKNOWN_VALUES:
        return UNKNOWN_SPECIES
    text = REWRITES.get(lowered, text)
    if not LATIN.match(text):
        COUNTS.unparsed[text] += 1
        return None
    return text


def parse_dbh(raw) -> float | None:
    """`DBH` is a string of inches; unreadable values are counted."""
    text = clean(raw)
    if text is None:
        return None
    COUNTS.dbh_seen += 1
    try:
        return float(text)
    except ValueError:
        COUNTS.dbh_unparsed[text] += 1
        return None


def tree_number(value) -> str:
    return f"{ID_PREFIX}-{clean(value).replace(' ', '-')}"


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
    planted: list[date | None] = []
    latitude: list[float] = []
    longitude: list[float] = []
    dbh: list[float | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        tree_id.append(tree_number(attrs["UNIQUEID"]))
        species.append(parse_species(attrs.get("SPP")))
        planted.append(None)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(parse_dbh(attrs.get("DBH")))

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["USSMQ"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "plant_date": pa.array(planted, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


if __name__ == "__main__":
    table = stream_to_table(iter_chunks(), transform, keep=keep, label="San Mateo OpenData")
    dropped = ", ".join(f"{n:,} {why}" for why, n in COUNTS.dropped.most_common())
    print(
        f"San Mateo ingest: dropped {sum(COUNTS.dropped.values()):,} rows that are "
        f"not trees ({dropped}), {COUNTS.no_id} with no UNIQUEID, "
        f"{COUNTS.no_geometry} with no geometry",
        file=sys.stderr,
    )
    unparsed = sum(COUNTS.unparsed.values())
    if unparsed:
        print(
            f"San Mateo ingest: {unparsed} species values unreadable: "
            f"{COUNTS.unparsed.most_common(10)}",
            file=sys.stderr,
        )
    if COUNTS.named and unparsed / COUNTS.named > MAX_UNPARSED_SHARE:
        raise RuntimeError(
            f"San Mateo ingest: {unparsed} of {COUNTS.named} species values "
            "unreadable; the SPP field's format has changed"
        )
    dbh_unparsed = sum(COUNTS.dbh_unparsed.values())
    if dbh_unparsed:
        print(
            f"San Mateo ingest: {dbh_unparsed} DBH values unreadable: "
            f"{COUNTS.dbh_unparsed.most_common(10)}",
            file=sys.stderr,
        )
    if COUNTS.dbh_seen and dbh_unparsed / COUNTS.dbh_seen > MAX_UNPARSED_SHARE:
        raise RuntimeError(
            f"San Mateo ingest: {dbh_unparsed} of {COUNTS.dbh_seen} DBH values "
            "unreadable; the DBH field's format has changed"
        )
    table = validate_coordinates(table, city="San Mateo", city_code="USSMQ")
    table = enforce_tree_schema(
        table, city="San Mateo", data_source="SAN_MATEO_OPENDATA"
    )
    emit(table)

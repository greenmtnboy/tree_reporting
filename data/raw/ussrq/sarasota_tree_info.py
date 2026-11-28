#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Sarasota County, Florida's County-maintained trees, from the County's own ArcGIS Server.

LICENCE: no licence stated; displayed with attribution only, not licensed for
reuse.  The item's `licenseInfo` is the County's "GIS Data Disclaimer and
Limitation of Liability" (no warranty, "use of these materials and data
constitutes acceptance of this disclaimer") and nothing else; the County's
open-data hub lists the dataset with licence "custom", the same disclaimer
(https://data-sarco.opendata.arcgis.com/datasets/d83796771ba64ea88f958c058ddcfa79,
https://www.arcgis.com/home/item.html?id=d83796771ba64ea88f958c058ddcfa79).

Source: "TreeInventory" (ArcGIS item d83796771ba64ea88f958c058ddcfa79, owner
`dcoley_sarco` of the County's organisation "Sarasota County GIS"), the
hosted layer `Hosted/TreeInventory/FeatureServer/0` on ags3.scgov.net: 60,047
rows on 2026-11-27, "supports the Sarasota County Trees Ordinance and
associated Canopy Road program".  It is the County's own trees, not every
tree in the county: `facilitytype` is County Right Of Way (51,487), County
Park Facility (5,026), County Public Facility (2,815) and County Utility
Facility (596).  Most rows are in unincorporated Sarasota County, with some
County road and park trees inside the cities of Sarasota, Venice and North
Port (the `city` attribute names them on ~3,400 rows; it is blank on the
rest and is not read).  `ownerentity` is mostly empty; where it is filled it
is the County, a boundary tree or a private tree beside County land, and all
are kept as trees.  The layer is stored in Florida West State Plane (feet,
wkid 2882) and read in WGS84 (`outSR=4326`).

**The id is `treeid`** (`sarasota-{treeid}`): `TI_{MMDDYYYY}_{n}` for right-of-
way trees (the 2009 inventory load and later additions) and `FT_...` for
facility trees, non-null and unique over the whole layer (60,047 distinct on
2026-11-27).  The OBJECTID is a dense 1..60,047.  A row with no `treeid` is
dropped with a logged count; a repeat raises (the schema's grain check).

**Which rows are trees.**  `existstatus = Removed` (2,588 rows) are removed
trees still in the layer and are dropped; `In Place` and empty are standing.
`conditionrating = Dead` (403) are still standing and stay.

**Species is `genusname`**, a coded-value domain whose codes are the
botanical binomial in title case (`Quercus Virginiana`, `Lagerstroemia Spp`);
the shared sanitiser lower-cases the epithet and folds `Spp` to the genus.
An empty `genusname` (9,520 rows, ~240 of them with only a `commonname`) is
an unidentified tree and publishes with no species; `commonname` is never
mapped.  A value that does not read as a Latin name counts as unreadable;
above 1% the build is refused.

**DBH is `diameterbreastheight`, inches** (whole inches: median 10, 99th
percentile 38; a Florida County inventory).  99 is the layer's
fill value (it is the maximum of every integer field, 703 times in
`growthclerance`) and is nulled; 0 is unmeasured and nulled by the schema.

**`dateplanted`** is recorded on 14,898 right-of-way trees (1982-2023, often
the first of a month); the nine 1970-01-06 values are an epoch stamp, not a
planting, and are nulled.  `treeheight` (feet) is recorded but the schema has
no height column; no crown spread is recorded (`crownclass` is empty).
"""

import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_date, esri_point, iter_features  # noqa: E402
from shared.ingest import (  # noqa: E402
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

SOURCE_URL = "https://ags3.scgov.net/server/rest/services/Hosted/TreeInventory/FeatureServer/0"
LAYER = FeatureLayer(SOURCE_URL, timeout=180)
OUT_FIELDS = "objectid,treeid,genusname,diameterbreastheight,dateplanted,existstatus"
ID_PREFIX = "sarasota"

REMOVED = "removed"
DBH_FILL = 99
# Plant dates before this are the epoch stamp (1970-01-06), not plantings.
PLANT_DATE_MIN = date(1971, 1, 1)

MAX_UNPARSED_SHARE = 0.01

# Genus, then words (epithet or `Spp`); the domain writes the epithet capitalised.
LATIN = re.compile(r"^[A-Z][a-z]+( [A-Za-z][a-z.-]*)*$")


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
    stamped_dates = 0
    dbh_fill = 0


COUNTS = Counts()


def keep(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    status = (clean(attrs.get("existstatus")) or "").lower()
    if status == REMOVED:
        COUNTS.dropped["existstatus Removed"] += 1
        return False
    if clean(attrs.get("treeid")) is None:
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
        "Quercus Virginiana" -> "Quercus Virginiana"  (sanitiser: Quercus virginiana)
        "Lagerstroemia Spp"  -> "Lagerstroemia Spp"   (sanitiser: Lagerstroemia)
        None                 -> None
    """
    text = clean(raw)
    if text is None:
        return None
    COUNTS.named += 1
    if not LATIN.match(text):
        COUNTS.unparsed[text] += 1
        return None
    return text


def parse_dbh(value) -> float | None:
    if value is None:
        return None
    number = float(value)
    if number == DBH_FILL:
        COUNTS.dbh_fill += 1
        return None
    return number


def parse_plant_date(value) -> date | None:
    planted = esri_ms_to_date(value)
    if planted is not None and planted < PLANT_DATE_MIN:
        COUNTS.stamped_dates += 1
        return None
    return planted


def iter_chunks():
    yield from iter_features(
        LAYER,
        out_fields=OUT_FIELDS,
        return_geometry=True,
        out_sr=4326,
        order_by="objectid",
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
        tree_id.append(f"{ID_PREFIX}-{clean(attrs['treeid'])}")
        species.append(parse_species(attrs.get("genusname")))
        planted.append(parse_plant_date(attrs.get("dateplanted")))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(parse_dbh(attrs.get("diameterbreastheight")))

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["USSRQ"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "plant_date": pa.array(planted, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


if __name__ == "__main__":
    table = stream_to_table(iter_chunks(), transform, keep=keep, label="Sarasota County OpenData")
    dropped = ", ".join(f"{n:,} {why}" for why, n in COUNTS.dropped.most_common())
    print(
        f"Sarasota County ingest: dropped {sum(COUNTS.dropped.values()):,} rows that are "
        f"not standing trees ({dropped or 'none'}), {COUNTS.no_id} with no treeid, "
        f"{COUNTS.no_geometry} with no geometry; nulled {COUNTS.stamped_dates} stamped "
        f"plant dates and {COUNTS.dbh_fill} DBH fill values",
        file=sys.stderr,
    )
    unparsed = sum(COUNTS.unparsed.values())
    if unparsed:
        print(
            f"Sarasota County ingest: {unparsed} species values unreadable: "
            f"{COUNTS.unparsed.most_common(10)}",
            file=sys.stderr,
        )
    if COUNTS.named and unparsed / COUNTS.named > MAX_UNPARSED_SHARE:
        raise RuntimeError(
            f"Sarasota County ingest: {unparsed} of {COUNTS.named} species values "
            "unreadable; the genusname field's format has changed"
        )
    table = validate_coordinates(table, city="Sarasota County", city_code="USSRQ")
    table = enforce_tree_schema(
        table, city="Sarasota County", data_source="SARASOTA_COUNTY_OPENDATA"
    )
    emit(table)

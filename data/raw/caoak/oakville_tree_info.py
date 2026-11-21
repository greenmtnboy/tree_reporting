#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Oakville's (Ontario) town tree inventory, from the Town of Oakville's open data.

Source: "Trees" (item 64fabcdb4feb40139ad20d728bfef2d4, owned by the Town's
`Oakville_GIS` account and shared to the Town of Oakville Open Data Portal),
served from the Town's own ArcGIS Server as `PGIS_BULK/Parks_Tree_Forestry`
layer 0.  The item's licence field names the Town of Oakville Open Data
Licence (https://www.oakville.ca/town-hall/town-studies-plans-projects/town-initiatives/open-data/open-data-licence/),
based on the Open Government Licence - Canada 2.0: copy, adapt and use
commercially, acknowledging the source; with no attribution statement of the
provider's own, it reads "Contains information licensed under the Open
Government Licence - Town of Oakville."

**One layer holds every Town tree**: `LOCSITE` is Front Yard, Park Tree,
Boulevard, Sideyard, Woodland Tree (woodlot trees, 11k kept), Median,
Planting Strip and Cemetery Tree.  `OWNERSHIP` is mostly the Town, some
Region of Halton (regional-road medians and strips) and a few hundred
Private; all are kept -- the Town inventories and maintains them.

**Only `STATUS` EXISTING and SCHEDULED FOR REMOVAL are trees in the
ground.**  Measured 2026-11-20 over 219,786 rows: EXISTING 155,748, REMOVED
60,586, ON A PLANTING LIST 1,726 (planned plantings, no DBH),
POTENTIAL PLANTING 1,259 (candidate sites), SCHEDULED FOR REMOVAL 453 and 14
with no status (each a measured tree with a species; kept).  The filter is
server-side.  `STUMPING_DATE` is stamped 1999 on 23.5k EXISTING rows -- the
same load default as the planting date below -- so it is not read as removal.

**The id is `GLOBALID`** (`oakville-{guid}`): non-null and unique over all
219,786 rows (checked 2026-11-20), and the layer is edited in place
(OBJECTID runs to 3.87M with 220k rows).  `FACILITYID` ("TreeID") repeats
(256 values among kept rows, one 49 times; `M-STEM (3)` for multi-stem
groups) and is not used.  A row without a GLOBALID is dropped with a count.

**Species is free text in "Common name - Latin name" form** (`Maple, Norway
- Acer platanoides`).  The field has a coded-value domain, but the codes are
those same strings and ~90 values in use are not in it (typed variants:
`Kentucky, Coffee Tree - Gymnocladus dioica`, en dashes, `(European)-
Carpinus`).  The Latin part is the text after the last spaced dash, first
line only (one value was pasted twice across a newline); a parenthetical
(`Gymnocladus dioicus (dioica)`) is dropped.  A value with no dash is kept
only when it reads as a binomial (`Ginkgo biloba`), else the part after its
last comma (`Amur Maackia, Maackia amurensis`); a bare common name
(`White Oak`, 4 rows) publishes as Unknown.  `Shrub - Shrub variety` (4.1k)
is the Shrub sentinel.  A cross written as two parents (`Ulmus japonica x
wilsoniana 'Morton'`, the Accolade elm, 3.4k; `Acer truncatum x A.
plantanoides 'Keithsform'`; `Salix alba X fragilis`) is its genus with the
cultivar kept, as the shared English table files Accolade; read as written
the schema would truncate it to the first parent; a cultivar typed as the
epithet (`Ulmus Homestead`, `Ulmus morton`) is moved to the cultivar
(`CULTIVAR_AS_EPITHET`).  The common-name half is never mapped.

**DBH is centimetres** (`DBH`; median 17, p10 5, p99 89 over kept rows --
new plantings at 5-6).  Zero is unmeasured and is nulled by the schema.

**`CROWN_WIDTH` is the crown spread in metres** (whole metres, median 4, p99
18, max 40; it tracks DBH, r = 0.81, about 8 m at 30 cm DBH), published as
`crown_width_m`.  `HEIGHT` (metres) has no canonical column and is not read.

**`PLANTING_DATE` is mostly season-stamped** (October 30 or April 30 of the
year, sometimes November 30): kept, since the year is the information.
**1999-11-30 is a load default** -- 23,360 kept rows, the only date in 1999,
paired with a 1999 `STUMPING_DATE` -- and is nulled.  A year-0202 typo falls
to the schema's 1500 floor.  Dates are midnight Toronto time stored as UTC
(05:00 or 09:00Z), so the UTC date is the local date.

Geometry is stored in UTM 17N (EPSG:26917) and requested in WGS84.
"""

import re
import sys
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
    cm_to_inches,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://maps.oakville.ca/oakgis/rest/services/PGIS_BULK/"
    "Parks_Tree_Forestry/FeatureServer/0",
    timeout=300,
)
OUT_FIELDS = "OBJECTID,GLOBALID,SPECIES,DBH,CROWN_WIDTH,PLANTING_DATE"
WHERE = "STATUS IN ('EXISTING', 'SCHEDULED FOR REMOVAL') OR STATUS IS NULL"

# The load default: every planting date in 1999 is this one day.
PLACEHOLDER_PLANT_DATE = date(1999, 11, 30)

# Cultivar or hybrid-group names this register types where the epithet goes.
# Read as written they would publish as species (`Ulmus homestead`).
CULTIVAR_AS_EPITHET = {
    "ulmus homestead": "Ulmus 'Homestead'",
    "ulmus morton": "Ulmus 'Morton'",
    "populus hybrid poplar": "Populus",
}

# A dash with whitespace on at least one side separates the common name from
# the Latin one; a hyphen inside a word (`Pea-Tree`, `'espresso-jfs'`) does not.
_SEPARATOR = re.compile(r"\s[-–—]\s*|[-–—]\s")
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)")
_BINOMIAL = re.compile(r"^[A-Z][a-z]+ (x )?[a-z]")
_SPECIES_CROSS = re.compile(
    r"^([A-Z][a-z]+) [a-z]+ [xX] (?:[A-Za-z]\. )?[a-z]+\s*('[^']*')?\s*$"
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def latin_name(value) -> str | None:
    """`Maple, Norway - Acer platanoides` -> `Acer platanoides`."""
    if value is None:
        return None
    first_line = str(value).strip().splitlines()[0] if str(value).strip() else ""
    text = clean(first_line)
    if text is None:
        return None
    parts = _SEPARATOR.split(text)
    if len(parts) > 1:
        latin = clean(parts[-1]) or clean(parts[-2])
    elif _BINOMIAL.match(text):
        latin = text
    else:
        tail = clean(text.rsplit(",", 1)[-1])
        latin = tail if tail and _BINOMIAL.match(tail) else None
    if latin is None:
        # A bare common name, or a sentinel the schema reads (`Dead`).
        return text if text.lower() in {"dead", "unknown", "vacant", "stump"} else None
    # `Quercus robur, 'Fastigiata'`: a comma before the cultivar.
    latin = clean(_PARENTHETICAL.sub("", latin).replace(",", " "))
    if latin is None:
        return None
    if latin.lower() == "shrub variety":
        return "Shrub"
    latin = CULTIVAR_AS_EPITHET.get(latin.lower(), latin)
    # A cross of two named species (`Ulmus japonica x wilsoniana 'Morton'`,
    # the Accolade elm) is the genus, as the English table files it; read
    # as written the schema would truncate it to the first parent.
    cross = _SPECIES_CROSS.match(latin)
    if cross:
        return clean(f"{cross.group(1)} {cross.group(2) or ''}")
    return latin


def diameter_inches(value) -> float | None:
    if value is None:
        return None
    number = float(value)
    return cm_to_inches(number) if number > 0 else None


def crown_metres(value) -> float | None:
    return None if value is None else float(value)


def plant_date(value) -> date | None:
    planted = esri_ms_to_date(value)
    return None if planted == PLACEHOLDER_PLANT_DATE else planted


def global_id(rec: dict) -> str | None:
    value = clean(rec.get("GLOBALID"))
    return value.strip("{}").lower() if value else None


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    planted: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        tree_id.append(f"oakville-{global_id(rec)}")
        species.append(latin_name(rec.get("SPECIES")))
        planted.append(plant_date(rec.get("PLANTING_DATE")))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(diameter_inches(rec.get("DBH")))
        crown.append(crown_metres(rec.get("CROWN_WIDTH")))

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CAOAK"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array(planted, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            "crown_width_m": pa.array(crown, type=pa.float64()),
        }
    )


_unidentified = 0


def has_id(feature: dict) -> bool:
    global _unidentified
    if global_id(feature.get("attributes") or {}) is None:
        _unidentified += 1
        return False
    return True


if __name__ == "__main__":
    pages = iter_features(
        LAYER,
        out_fields=OUT_FIELDS,
        where=WHERE,
        return_geometry=True,
        extra_params={"geometryPrecision": "7"},
    )
    table = stream_to_table(pages, transform, keep=has_id, label="Oakville OpenData")
    if _unidentified:
        print(
            f"Oakville ingest: dropped {_unidentified:,} rows with no GLOBALID",
            file=sys.stderr,
        )
    table = validate_coordinates(table, city="Oakville", city_code="CAOAK")
    table = enforce_tree_schema(
        table, city="Oakville", data_source="OAKVILLE_OPENDATA"
    )
    emit(table)

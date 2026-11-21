#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Belfast's council trees, from Belfast City Council's own open-data export.

Source: "Belfast Trees" on OpenDataNI (CKAN package `belfast-trees`,
organisation Belfast City Council, resource
b501ba21-e26d-46d6-b854-26a95db78dd9), a CSV the council hosts on
belfastcity.gov.uk ("Trees-Open-Data.csv"; `Last-Modified` 2026-09-10, 56,521
rows on 2026-11-20).  The package's licence: "UK Open Government Licence
(OGL)", https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/.
The download URL is read from the resource record (`resource_download_url`)
rather than pasted, so a re-upload under a new asset path does not break it.
The Queen's University ArcGIS copy ("Belfast_Trees_Open_Data", 37,557 rows)
is a stale mirror and is not read.

Street trees (`TreeType = StreetTree`, ~16k) and park trees (`ParkTree`,
~40k) are one file.  Columns: `TreeType, SpecieGroup, Specie, AGE, Vigour,
Condition, DBH, Spread, Longitude, Latitude, TreeTag, Height`.

**The id is the rounded coordinate (`belfast-{lon:.7f}_{lat:.7f}`)**
(EXTENDING.md step 1, as Spokane and Philadelphia).  The file has no asset id:
`TreeTag` is a field tag number, NULL on 13,522 rows, `0` on 3,292 and only
5,747 distinct values over the other ~43,000 (checked over the whole file on
2026-11-20).  Coordinates are WGS84 at 6 dp, space-padded strings.  Rows that
share a point are dropped by `drop_stacked_coordinates` unless they are the
same tree twice (same species and diameter), when one is kept.

**Felled trees are dropped.**  `AGE = Felling` (8,222 rows) marks a tree that
has been felled: 8,193 of them carry a *negated* DBH (`-18`) and most a zero
height, which no standing tree has.  They are counted and dropped.  `Dead` and
`Dying` (`Condition`) are trees and stay.

**`Specie` is the scientific name**, with the source's quoting slips repaired
here (`SPELLING_FIXES`, `_PARENTHETICAL`): a parenthesised English qualifier
(`Acer saccharinum (Silver)`), double-quoted cultivars, an unquoted cultivar,
`Cotoneaster type`, and two English names keyed into the Latin column (`Field
maple`, `Lawson cypress`, 79 rows).  Real misspellings (`Tilla` for *Tilia*,
`Gingko`, `Notofagus` ...) are left for `SPECIES_MISSPELLINGS`.  `NULL`/`N/A`
publish as Unknown; the `Mixed broadleaf` / `Mixed coniferous` rows (430) are
tree records whose species was not recorded and publish as the unknown
sentinels.  Quoted cultivars are left in for `enforce_tree_schema` to split.
`SpecieGroup` is an English genus label and is not mapped.

**`DBH` is a diameter in centimetres** (median by age class: Young ~10,
Semi-Mature ~29, Mature ~50, Fully Mature ~80, Over Mature ~108 on beech,
sycamore, lime and oak), divided by 2.54.  The schema nulls zero, negative and
>200 in.  **`Spread` is a crown *radius* in metres**: at 45-55 cm DBH it
averages 4.8 against Nottingham's 9.4 m `Crown_Spread_m` (whose
`Crown_Radius_m` is exactly half), and 8.2 against 17.5 at 105-115 cm, so
`crown_width_m = 2 * Spread`.  `Height` (m) has no canonical column.  There is
no planting date.  A DBH or Spread that is neither numeric nor NULL is
counted, and over 1% of rows refuses the run.

**About 3,200 standing trees lie outside Belfast's box** -- street trees in
Newtownabbey, Holywood, Newtownards, Bangor, Lisburn and Ballymena -- plus 35
placeholder rows at (51.22, -10.86) off Kerry; `validate_coordinates` drops
them against `CITY_BOUNDS`.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    UNKNOWN_CONIFER_SPECIES,
    UNKNOWN_DECIDUOUS_SPECIES,
    UNKNOWN_SPECIES,
    coordinate_key,
    coordinate_tree_id,
    drop_stacked_coordinates,
    emit,
    enforce_tree_schema,
    iter_csv_row_chunks,
    stream_to_table,
    validate_coordinates,
)
from shared.platforms.ckan import CkanResource, resource_download_url

# Kept in step with belfast_update_time.py.
RESOURCE = CkanResource("admin.opendatani.gov.uk", "b501ba21-e26d-46d6-b854-26a95db78dd9")

REQUIRED_FIELDS = ("Specie", "AGE", "DBH", "Spread", "Longitude", "Latitude")

FELLED = "Felling"
NO_VALUE = frozenset({"", "NULL", "N/A"})

# Refuse a run where more than this share of rows carries an unreadable number.
MAX_UNPARSED_FRACTION = 0.01

# Source slips that are not misspellings of a name (those belong in
# SPECIES_MISSPELLINGS): quoting, a placeholder word, an English name in the
# Latin column, and the 'mixed' placeholders.
SPELLING_FIXES: dict[str, str] = {
    "Cotoneaster type": "Cotoneaster",
    "Salix matsudana Tortuosa": "Salix matsudana 'Tortuosa'",
    "Cedrus atlantica glauca": "Cedrus atlantica 'Glauca'",
    "Field maple": "Acer campestre",
    "Lawson cypress": "Chamaecyparis lawsoniana",
    "Mixed broadleaf": UNKNOWN_DECIDUOUS_SPECIES,
    "Mixed broadleaf and coniferous": UNKNOWN_SPECIES,
    "Mixed coniferous": UNKNOWN_CONIFER_SPECIES,
}

# `Acer saccharinum (Silver)`, `Ilex acqufolium (Variegated)`.
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)\s*$")

DROPPED = {"felled": 0, "no location": 0}
UNPARSED = {"DBH": 0, "Spread": 0, "rows": 0}


def clean_species(value: str | None) -> str | None:
    text = " ".join((value or "").split())
    if text in NO_VALUE:
        return None
    text = SPELLING_FIXES.get(text, text)
    text = _PARENTHETICAL.sub("", text).replace('"', "'")
    return text or None


def number(value: str | None, field: str) -> float | None:
    text = (value or "").strip()
    if text in NO_VALUE:
        return None
    try:
        return float(text)
    except ValueError:
        UNPARSED[field] += 1
        return None


def coordinate(value: str | None) -> float | None:
    text = (value or "").strip()
    if text in NO_VALUE:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def keep(rec: dict) -> bool:
    if (rec.get("AGE") or "").strip() == FELLED:
        DROPPED["felled"] += 1
        return False
    if coordinate(rec.get("Latitude")) is None or coordinate(rec.get("Longitude")) is None:
        DROPPED["no location"] += 1
        return False
    return True


def iter_row_chunks():
    """The council CSV in chunks, after checking the header still names our fields."""
    for chunk in iter_csv_row_chunks(resource_download_url(RESOURCE), delimiter=","):
        if chunk:
            missing = [f for f in REQUIRED_FIELDS if f not in chunk[0]]
            if missing:
                raise RuntimeError(
                    f"Belfast tree CSV no longer has {missing}; header is {sorted(chunk[0])}"
                )
        yield chunk


def transform(rows: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    latitude: list[float] = []
    longitude: list[float] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []

    for rec in rows:
        UNPARSED["rows"] += 1
        lon, lat = coordinate_key(coordinate(rec["Longitude"]), coordinate(rec["Latitude"]))
        tree_id.append(coordinate_tree_id("belfast", lon, lat))
        species.append(clean_species(rec.get("Specie")))
        latitude.append(lat)
        longitude.append(lon)
        dbh_cm = number(rec.get("DBH"), "DBH")
        dbh.append(dbh_cm / 2.54 if dbh_cm is not None else None)
        radius = number(rec.get("Spread"), "Spread")
        crown.append(2 * radius if radius is not None and radius > 0 else None)

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["GBBFS"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array([None] * n, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            "crown_width_m": pa.array(crown, type=pa.float64()),
        }
    )


def check_parsing() -> None:
    rows = UNPARSED["rows"]
    for field in ("DBH", "Spread"):
        bad = UNPARSED[field]
        if rows and bad / rows > MAX_UNPARSED_FRACTION:
            raise RuntimeError(
                f"Belfast: {bad} of {rows} rows have an unreadable {field} "
                f"(> {MAX_UNPARSED_FRACTION:.0%}); the export's format changed"
            )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, keep=keep, label="Belfast OpenData")
    print(f"Belfast: dropped {DROPPED}; unreadable numbers {UNPARSED}", file=sys.stderr)
    check_parsing()
    table = validate_coordinates(table, city="Belfast", city_code="GBBFS")
    table = drop_stacked_coordinates(
        table, label="Belfast OpenData", identical=("species", "diameter_at_breast_height")
    )
    table = enforce_tree_schema(table, city="Belfast", data_source="BELFAST_OPENDATA")
    emit(table)

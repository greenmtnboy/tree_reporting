#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Halifax's public tree inventory, from the HRM open data catalogue.

Source: "Public Trees" (`Public_Trees`) on `catalogue-hrm.opendata.arcgis.com`,
80,051 rows covering the whole Halifax Regional Municipality -- which is why
CAHFX's bounding box spans 1.3 degrees of longitude.  Paging, the freshness
watermark and Esri's epoch-milliseconds live in `_arcgis_shared`.

Three things about this source are worth knowing.

**`TREEID` is a real per-tree key.**  Checked over the whole table rather than
a first page, the way `EXTENDING.md` asks: 80,051 distinct values across
80,051 rows, none null, none blank.  `ASSETID` carries the identical string
and `GLOBALID` is equally clean, so any of the three would have worked;
`TREEID` is the one the city names for the thing.

**`DBH` is a size class, not a measurement.**  It runs 1 to 9 with a median of
2, which would be a nine-centimetre median tree if read as centimetres.  The
layer publishes the actual meaning as a coded-value domain on the field
(`AST_tree_dbh`), and `DBH_CLASS_MIDPOINT_CM` below is that domain converted
to midpoints -- the same reconstruction Denver's six-inch buckets get, for the
same reason: leaving the whole city null would drop it out of every size chart
on the summary page, and a midpoint is wrong by a few centimetres per tree and
right in aggregate.

**`SP_SCIEN` identifies 7,700 trees only to genus**, and says so with a
`(genus)` suffix -- `Acer (genus)`, `Tilia (genus)`.  `sanitize_species`
strips the parenthetical and keeps the genus, which is the honest reading.
Twenty-nine of the thirty spellings are real genera; the thirtieth is
`Elm (genus)`, an English common name, which `_NON_TAXON_REWRITES` maps to
Ulmus rather than letting "Elm" through as an invented genus.

**About 1,000 rows carry a shorthand code instead of a name**, and they have
to be dropped -- see `parse_species`.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, iter_features
from _ingest_shared import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    normalize_species,
    normalize_tree_name,
    parse_plant_date_year,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services2.arcgis.com/11XBiaBYA9Ep0yNJ/arcgis/rest/services/"
    "Public_Trees/FeatureServer/0",
    timeout=180,
)

# The layer publishes no lat/lon columns, so the geometry is what carries the
# position; `out_sr=4326` reprojects it server-side out of the layer's Web
# Mercator storage.
OUT_FIELDS = "TREEID,SP_SCIEN,SP_COMM,DBH,INSTYR,FCODE"

# `DBH` is a coded value, and these are the codes -- read off the layer's own
# `AST_tree_dbh` domain (`?f=json`, `fields[].domain`) rather than guessed:
#
#     1  0 to 7.9 cm      4  31 to 45.9 cm    7  77 to 90.9 cm
#     2  8 to 15.9 cm     5  46 to 60.9 cm    8  91 to 106.9 cm
#     3  16 to 30.9 cm    6  61 to 76.9 cm    9  >107 cm
#
# The midpoint is the honest reconstruction of a bucketed value.  Class 9 is
# open-topped and gets its lower bound plus half the width the classes settle
# at (~15.5 cm), which is the rule Denver's "48 +" bucket takes.
#
# An unknown code returns None rather than raising: the layer holds one row
# coded 11, which is not in its own domain, and one junk cell is a tree we
# cannot size rather than a reason to fail the city's refresh.
DBH_CLASS_MIDPOINT_CM: dict[int, float] = {
    1: 4.0,
    2: 12.0,
    3: 23.5,
    4: 38.5,
    5: 53.5,
    6: 69.0,
    7: 84.0,
    8: 99.0,
    9: 115.0,
}


def parse_species(value) -> str | None:
    """Halifax's `SP_SCIEN`, with its shorthand codes dropped to Unknown.

    Roughly a thousand rows carry a 4-6 letter contraction of the binomial
    instead of the binomial -- `ACRU`, `QURU`, `TIAM`, `GLTR`, `PLAC` -- and
    the short ones are shaped exactly like a genus.  Left alone they publish
    as invented genera (`Acru`, `Quru`), which is worse than saying nothing:
    the species key is the join into the enrichment table, so each becomes a
    permanent LLM call and a wrong label on every tree carrying it.  That is
    the Orania failure described in EXTENDING.md, in miniature and thirty
    times over.

    **The layer separates the two itself, and the separation was checked over
    all 328 distinct values.**  Halifax writes a real name in title case
    (`Acer rubrum`) and qualifies a genus-only identification explicitly
    (`Acer (genus)`); every value that is a single bare word is upper-case and
    is a code.  So a single all-caps token is the rule, and it is written as
    two conditions rather than one so that a genuine `Cryptomeria` appearing
    later is not caught by it.

    Decoding the codes is deliberately not attempted.  Some are close to USDA
    PLANTS symbols and some are not (`ACRUMO`, `ACSPI`, `QUEBI`, `GIBIM`), so
    a mapping would be a guess at a taxon -- exactly the thing the species key
    must never carry.  Unknown keeps those trees on the map, and honest.
    """
    if value is None:
        return None
    text = " ".join(str(value).split())
    if text and " " not in text and text.isupper():
        return None
    return normalize_species(text)


def parse_dbh(value) -> float | None:
    """A DBH size class to the midpoint of its band, in inches."""
    try:
        code = int(value)
    except (TypeError, ValueError):
        return None
    return cm_to_inches(DBH_CLASS_MIDPOINT_CM.get(code))


def iter_row_chunks():
    """One ArcGIS page at a time.

    A generator rather than a bulk read, which is the house rule for a paged
    source: holding every feature as a response body plus a dict each is what
    OOM-killed Washington DC's 2 GiB container at 216k.
    """
    return iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    )


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    tree_name: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        geom = feature.get("geometry") or {}
        raw_id = str(rec.get("TREEID") or "").strip()
        if not raw_id:
            continue

        tree_id.append(f"hfx-{raw_id}")
        species.append(parse_species(rec.get("SP_SCIEN")))
        tree_name.append(normalize_tree_name(rec.get("SP_COMM")))
        # INSTYR is the planting year, and 3,930 rows record it as 0 -- an
        # inventory's way of saying "before we started counting".
        # parse_plant_date_year returns None for that rather than year zero.
        plant_date.append(parse_plant_date_year(rec.get("INSTYR")))
        latitude.append(geom.get("y"))
        longitude.append(geom.get("x"))
        dbh.append(parse_dbh(rec.get("DBH")))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CAHFX"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "tree_name": pa.array(tree_name, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    # Nothing is filtered out here, and that is a measurement rather than an
    # omission: `FCODE` can encode a stump or a vacant planting site, and
    # Halifax's holds only LCTS (single tree, 78,953), LCDS (dead tree, 1,013)
    # and LCTA (grove, 85).  A dead tree is a tree that is there, so it stays --
    # see "An empty site is not an unidentified tree" in EXTENDING.md.
    table = stream_to_table(iter_row_chunks(), transform, label="Halifax OpenData")
    table = validate_coordinates(table, city="Halifax", city_code="CAHFX")
    table = enforce_tree_schema(
        table, city="Halifax", data_source="HALIFAX_OPENDATA"
    )
    emit(table)

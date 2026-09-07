#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Ottawa's street and park tree inventory, from open.ottawa.ca.

Source: "Tree Inventory" (`Forestry/MapServer/0`) on the city's on-prem ArcGIS
Server, 304,374 rows over a municipality that reaches from the Ottawa River to
farmland -- which is why CAOTT's bounding box spans half a degree of latitude.
Paging and Esri's epoch-milliseconds live in `_arcgis_shared`.

**The `SPECIES` column looks like a common name and is not the whole story.**
It stores an inverted English name -- `Maple Sugar`, `Lilac Japanese`,
`Spruce Blue/Colorado` -- and the handoff that planned this batch recorded it
as "common name only, not a binomial", which cost the plan a city.  It is a
coded-value field: the layer's own domain maps every one of those 174 stored
codes to the binomial, `Acer saccharum`, `Syringa reticulata`, `Picea
pungens`.  Ottawa's foresters published the identification; it just lives in
`fields[].domain` rather than in a column.  `coded_value_domain` reads it, so
this city needs no part of `_common_name_species` -- it is here as the
counter-example to the rest of the batch: **read the field's domain before
concluding a portal does not identify its trees.**

Two of the domain's 174 names are misspelled at the source (`Cercidiphyllum
japonic`, `Liriodendron tulipifer`), and a misspelled binomial is one
`sanitize_species` deliberately keeps -- so left alone they would split the
katsura and the tulip tree away from every other city's spelling.  They are
corrected in `DOMAIN_SPELLING_FIXES` below, which is a two-entry list rather
than a rule because only reading the names finds them.

**Everything in the layer is `STATUS = 'Active'`; the column that matters is
`FLAG`**, aliased "Tree/Stump Removed?".  210 rows carry one (117 removed, 54
"tree does not exist", 12 duplicate records, and a handful of construction and
development codes) and are dropped; 304,164 are published.

**`TREEID` is a per-tree GUID, is populated on every row, and is not quite
unique** -- one value covers two rows, checked over the whole layer.  Two rows
is a rounding error and it would probably have gone unnoticed, which is the
point: `enforce_tree_schema` refuses a repeated `tree_id` because the grain
fans out every join built on it.  `GLOBALID` is distinct on all 304,374 rows
and is the runbook's first choice anyway, so it is the key here.

**No `tree_name` is published**, unlike the rest of this batch.  The stored
code *is* Ottawa's own common name, but inverted with nothing to invert on --
`Maple Sugar`, `Spruce Blue/Colorado`, `Mountain Ash European` -- and
`normalize_tree_name` only un-inverts a comma or a spaced hyphen, because a
space-separated inversion cannot be undone mechanically (the head of `Mountain
Ash European` is the first *two* words).  A card reading "Maple sugar" above
*Acer saccharum* is worse than no common name at all, and the enrichment table
supplies a proper one for every binomial the domain resolves.

The layer has no usable lat/lon columns -- `X_COORD`/`Y_COORD` are in the
server's own projection -- so the geometry is requested with `out_sr=4326` and
reprojected server-side.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import (
    FeatureLayer,
    coded_value_domain,
    esri_ms_to_date,
    esri_point,
    iter_features,
)
from _ingest_shared import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://maps.ottawa.ca/arcgis/rest/services/Forestry/MapServer/0",
    timeout=300,
)

OUT_FIELDS = "GLOBALID,SPECIES,DBH,PLNTDATE"

# Any value in the "Tree/Stump Removed?" field means the record no longer
# describes a standing tree -- or, for `DR`, never described a distinct one.
WHERE = "STATUS = 'Active' AND (FLAG IS NULL OR FLAG = '')"

# Typos in the publisher's own domain, corrected so these two taxa do not split
# away from the spelling every other city uses.  `Cercidiphyllum japonic` and
# `Liriodendron tulipifer` are each one letter short of the accepted name.
DOMAIN_SPELLING_FIXES: dict[str, str] = {
    "Cercidiphyllum japonic": "Cercidiphyllum japonicum",
    "Liriodendron tulipifer": "Liriodendron tulipifera",
}


def load_species_domain() -> dict[str, str]:
    """`{stored code: scientific name}` for `SPECIES`, spelling corrected.

    Read from the layer at run time rather than hardcoded: this is the layer's
    own data dictionary for the column being read, so a code Ottawa adds later
    resolves without an edit here.  A failure raises, which is what should
    happen -- an empty domain would publish 304k trees as `Unknown`.
    """
    domain = coded_value_domain(LAYER, "SPECIES")
    return {
        code: DOMAIN_SPELLING_FIXES.get(name, name) for code, name in domain.items()
    }


SPECIES_BY_CODE = load_species_domain()


def iter_row_chunks():
    """One ArcGIS page at a time; this MapServer caps a page at 1000."""
    return iter_features(
        LAYER,
        out_fields=OUT_FIELDS,
        where=WHERE,
        return_geometry=True,
        out_sr=4326,
    )


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        geom = feature.get("geometry") or {}
        raw_id = str(rec.get("GLOBALID") or "").strip()
        if not raw_id:
            continue
        code = (rec.get("SPECIES") or "").strip()

        tree_id.append(f"ott-{raw_id}")
        # An unrecognised code is `Unknown` rather than the code itself: the
        # stored value is not a taxon and publishing it would invent one.
        species.append(SPECIES_BY_CODE.get(code))
        plant_date.append(esri_ms_to_date(rec.get("PLNTDATE")))
        lat, lon = esri_point(geom)
        latitude.append(lat)
        longitude.append(lon)
        # `DBH` is aliased "Diameter (cm)".
        dbh.append(cm_to_inches(rec.get("DBH")))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CAOTT"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Ottawa OpenData")
    table = validate_coordinates(table, city="Ottawa", city_code="CAOTT")
    table = enforce_tree_schema(table, city="Ottawa", data_source="OTTAWA_OPENDATA")
    emit(table)

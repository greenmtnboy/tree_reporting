#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Nottingham's council-maintained trees, from the city council's ArcGIS Online.

Source: "Trees (NCC Maintained)" (ArcGIS item
854f59402043403eabce650cc7ab1150, owner `nccgisteam`), the hosted layer
`Trees__NCC_Maintained_/FeatureServer/63`: trees Nottingham City Council
maintains -- streets, parks, cemeteries, allotments, housing land.  45,166
rows on 2026-11-16.  The item states the Open Government Licence v3, with
the attribution "Nottingham City Council. Contains OS data (c) Crown
copyright and database right".  The item's description: "Tree information is
derived from human inspection.  Heights and spreads are recorded to the
nearest 2m, trunk dbh (diameter breast height) measurement is to the nearest
10cm estimated."  The layer is reloaded whole: every row's `Created_Date` is
2026-11-10, the day of the last load, so it is not a planting date and there
is no planting date in the layer.

**`Tree_ID` is the key** (`nott-{Tree_ID}`): the layer has no GlobalID, and
`Tree_ID`, the council's own asset number, is non-null and distinct on all
45,166 rows (checked over the whole layer on 2026-11-16).  A row without one
is dropped with a logged count rather than keyed on `OBJECTID`, which a
whole-layer reload renumbers.

**Location is the geometry, not `Lat`/`Long`.**  The layer is stored in
British National Grid (EPSG:27700) and read reprojected to WGS84; its `Lat`
and `Long` attributes are swapped (`Lat` holds -1.15), so they are not read.
Every point falls inside the city boundary.

**Not every row is a tree.**  The layer is the council's asset register, so
it also holds planting sites and stumps, and says so in two columns:

* `Species` values beginning with `!` -- `!!Potential Planting Pit ...`,
  `!!Existing Planting Pit ...`, `!Medium Replacement Species`,
  `!!!Stump: Species Undefined` (1,082 rows; their `Common_Name` starts with
  `!` too) -- and `Rejected Tree Pit Location (...)` (30 rows, a pit the
  utilities or a street lamp ruled out).  The shared not-a-tree rule does not
  know these spellings (and reads `Rejected ...` as a genus), so they are
  dropped here.
* `Age` of `Stump` (117), `Unused` (15, every dimension zero: a retired
  record), `New Location` (12, a planned position), `Tree Pit Exists` (3) and
  `~~~` (1).  `Unknown` (224) and blank ages are trees and stay.

**Species is scientific** (`Tilia x europaea`, `Acer pseudoplatanus`), with
the genus written `Prunus species` / `Pyrus spp.` for a tree identified only
to genus, which the schema reduces to the genus.  Cultivars are mostly
quoted (`Prunus 'Kanzan'`); this script splits them itself because several
contain an apostrophe (`Crataegus laevigata 'Paul's Scarlet'`, 45 rows) that
the shared parser would cut at.  An unquoted capitalised word after the
binomial is a cultivar too (`Fraxinus oxycarpa Raywood`,
`Acer pseudoplatanus Leopoldii`); a lower-case one is a form or variety
(`Fagus sylvatica purpurea`) and is left to the schema, which truncates it.
`REWRITES` maps the handful of values that name a cultivar or fruit variety
in the epithet's place (`Prunus victoria`, `Bramley`, `Laburnum vossii`).
`Unknown Species - Conifer` publishes as the `Unknown conifer` sentinel;
`Unknown Species - Broadleaf`, `No Species Set`, `Mixed Tree Group` and the
like as `Unknown`.  `Common_Name` is not mapped.

**DBH is `Trunk_DBH_cm`, a diameter in centimetres** (median 35, banded at
5/15/25/35/45 cm as the description's "nearest 10cm estimated" says; by age
class Young 15, Mature 55, Veteran 115), divided by 2.54.  Zero (1,845 rows,
most of them planting pits) is published as null.  Every value above
`DBH_MAX_CM` (19 rows) is a keying slip -- 325 cm on a semi-mature rowan,
1,325 on a whitebeam, 153,330 on a plum -- and is nulled here with a count.

**Crown width is `Crown_Spread_m`**, the spread in metres; `Crown_Radius_m`
is exactly half of it on every row with both.  Zero is nulled; the schema
nulls the one 98 m value.  `Height_m` has no canonical column.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    UNKNOWN_CONIFER_SPECIES,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services.arcgis.com/yvqphKcf9bBSnjX1/arcgis/rest/services/"
    "Trees__NCC_Maintained_/FeatureServer/63"
)

OUT_FIELDS = "OBJECTID,Tree_ID,Species,Common_Name,Age,Trunk_DBH_cm,Crown_Spread_m"

# `Age` values that record a site or a retired record, not a standing tree.
NOT_A_TREE_AGES = frozenset({"stump", "unused", "new location", "tree pit exists", "~~~"})

# `Species` values that record a pit the council ruled out.
REJECTED_PIT_PREFIX = "rejected tree pit"

# The largest diameter this layer records credibly, in cm.  The largest
# plausible values are 160-200 cm on Wollaton Park's veteran oaks and limes;
# everything above is a slip (see the module docstring).
DBH_MAX_CM = 220

# Values that put a cultivar or a fruit variety in the epithet's place, or
# name no taxon at all.  Keys compared after whitespace is collapsed.
REWRITES = {
    "Bramley": "Malus domestica 'Bramley's Seedling'",
    "Prunus fruit": "Prunus",
    "Prunus victoria": "Prunus domestica 'Victoria'",
    "Prunus opal": "Prunus domestica 'Opal'",
    "Prunus accolade": "Prunus 'Accolade'",
    "Prunus pissardii": "Prunus cerasifera 'Pissardii'",
    "Malus evereste": "Malus 'Evereste'",
    "Laburnum vossii": "Laburnum x watereri 'Vossii'",
    # The graft chimera, written both ways in the layer.
    "Laburnum adamii": "Laburnocytisus adamii",
    'Fraxinus "Jaspidea"': "Fraxinus excelsior 'Jaspidea'",
    "Corylus Chinoka": "Corylus",
    "Unknown Species - Conifer": UNKNOWN_CONIFER_SPECIES,
}

# A quoted cultivar: everything between the first quote and the last, so an
# apostrophe inside it ('Paul's Scarlet') survives.
_QUOTED = re.compile(r"""^(?P<taxon>[^'"‘’“”]*?)\s*['"‘’“”](?P<cultivar>.+)['"‘’“”]\s*$""")

# Tokens that may follow a binomial without being a cultivar.
_RANK_WORDS = frozenset({"x", "var", "var.", "v", "v.", "subsp", "subsp.", "f", "f.", "cultivar"})


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def split_species(raw) -> tuple[str | None, str | None]:
    """`(species string, cultivar)` from the layer's `Species` value."""
    text = clean(raw)
    if text is None:
        return None, None
    text = REWRITES.get(text, text)
    match = _QUOTED.match(text)
    if match:
        cultivar = clean(match["cultivar"].strip("'\"‘’“”"))
        return clean(match["taxon"]) or text, cultivar
    words = text.split()
    # `Fraxinus oxycarpa Raywood`, `Cotinus coggyria Royal Purple`: an
    # unquoted, capitalised name after the binomial.
    if (
        len(words) >= 3
        and words[1].islower()
        and words[1].lower() not in _RANK_WORDS
        and words[2][:1].isupper()
        and words[2].isalpha()
    ):
        return " ".join(words[:2]), " ".join(words[2:])
    return text, None


def is_tree(feature: dict) -> bool:
    rec = feature.get("attributes") or {}
    species = clean(rec.get("Species")) or ""
    common = clean(rec.get("Common_Name")) or ""
    if species.startswith("!") or common.startswith("!"):
        return False
    if species.lower().startswith(REJECTED_PIT_PREFIX):
        return False
    return (clean(rec.get("Age")) or "").lower() not in NOT_A_TREE_AGES


def iter_row_chunks():
    """One ArcGIS page (2,000 features) at a time, geometry in WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


_counts = {"no_id": 0, "dbh_slip": 0}


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        raw_id = clean(rec.get("Tree_ID"))
        if raw_id is None:
            _counts["no_id"] += 1
            continue
        lat, lon = esri_point(feature.get("geometry"))
        name, selection = split_species(rec.get("Species"))

        raw_dbh = rec.get("Trunk_DBH_cm")
        if raw_dbh is not None and float(raw_dbh) > DBH_MAX_CM:
            _counts["dbh_slip"] += 1
            raw_dbh = None
        spread = rec.get("Crown_Spread_m")

        tree_id.append(f"nott-{raw_id}")
        species.append(name)
        cultivar.append(selection)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(float(raw_dbh) / 2.54 if raw_dbh else None)
        crown.append(float(spread) if spread else None)

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["GBNOT"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array([None] * len(tree_id), type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            "crown_width_m": pa.array(crown, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(
        iter_row_chunks(), transform, keep=is_tree, label="Nottingham OpenData"
    )
    if _counts["no_id"]:
        print(f"Nottingham: dropped {_counts['no_id']} rows with no Tree_ID", file=sys.stderr)
    if _counts["dbh_slip"]:
        print(
            f"Nottingham: nulled {_counts['dbh_slip']} Trunk_DBH_cm values above {DBH_MAX_CM} cm",
            file=sys.stderr,
        )
    table = validate_coordinates(table, city="Nottingham", city_code="GBNOT")
    table = enforce_tree_schema(
        table, city="Nottingham", data_source="NOTTINGHAM_OPENDATA"
    )
    emit(table)

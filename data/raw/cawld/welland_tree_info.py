#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Welland's (Ontario) tree inventory, from the City of Welland.

Source: "Welland Trees" (ArcGIS item afcb6b5308de40b1830a3ebb2369a83b, owner
`WellandGIS`, "points representing the location of City-owned trees in the
City of Welland ... plotted using 2013 ortho imagery and verified in the
field"), listed on the City's open data portal open-welland.hub.arcgis.com
and served from the City's own ArcGIS Server as
`OpenData/OPENDATA_Active_Trees/MapServer/0`, stored in UTM 17N (EPSG 26917)
and reprojected server-side.  11,654 rows on 2026-11-22.  The description
calls the layer incomplete; it is the City's street-tree inventory.

LICENCE: Open Government Licence - City of Welland.  The item's
`licenseInfo` links https://open-welland.hub.arcgis.com/pages/terms-of-use,
which grants "a worldwide, royalty-free, perpetual, non-exclusive licence to
use the Information, including for commercial purposes" (copy, modify,
publish, translate, adapt, distribute), with the attribution statement
"Contains information licensed under the Open Government Licence – City of
Welland."  (Adapted from OGL-Canada 2.0 and OGL-Ontario 1.0.)

**The id is the rounded coordinate (`welland-{lon:.7f}_{lat:.7f}`)**, as for
Spokane and Barrie (EXTENDING.md, step 1).  The layer has no GlobalID and no
asset number; `OBJECTID` runs 1..11,654 without a gap, which is a layer
reloaded whole, so it would re-key on the next reload.  Rows sharing a 7 dp
coordinate are dropped (one kept where species and DBH agree); the one row
without geometry has no id and is dropped.

**Species is `Genus` (a Latin genus, free text) + `Species` (an English
common name, `Maple - Norway`)**, no coded-value domains.  The common name is
resolved through the shared English table (`shared.species.english`) and the
small `LOCAL_COMMON_NAMES` table below (named cultivars, which the shared
table folds to the taxon, and this inventory's typos), then checked against
the genus:

  * where they agree, the common name's (finer) answer is published;
  * where the common name resolves to nothing (`Robinia` + `Locust`,
    `Tilia` + `Tilia`), the City's genus is published alone;
  * where `Genus` is empty, `Other`, or a common name typed in the genus
    column (`Maple - Norway`), the common name decides;
  * where they disagree at genus level the row is a drop-down slip.  Two
    pairings are unambiguous and the common name wins (`Fraxinus` + `Ash -
    Mountain` is a mountain ash, *Sorbus*; `Castanea` + `Chestnut - Horse` is
    *Aesculus hippocastanum*); every other contradiction (`Acer` +
    `Sycamore`, `Robinia` + `Linden`, `Celtis` + `Locust`) publishes `Unknown`
    with a count, rather than either half.

**DBH is `DiamDBH`, whole centimetres** (median 22; Norway maple 26, silver
maple 46, the fall-2015 planting 6, a nursery caliper), converted to inches.
0 is not recorded (622 rows) and goes null; eight values from 211 to 5,544
cm are typing slips (no street tree in this inventory is two metres across)
and go null too, with a count.

**`plant_date` is `DatePlant`** (UTC midnight; 1,778 rows, 2011-2021).  Two
dates are stamps, not plantings: 2014-09-03 (473 rows, median DBH 28 cm, 389
of them over 15 cm) and 2014-08-27 (211 rows, median 16 cm) were put on
established trees in one session -- a 28 cm maple was not planted that week.
Both are nulled (`STAMPED_PLANT_DATES`).

The layer is "Active" trees: there is no status column and no removed, stump
or vacant-site rows to drop.  `Tree_Type`, `PlantArea`, `Address` and
`StName` have no canonical column.
"""

import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    cm_to_inches,
    coordinate_tree_id,
    drop_stacked_coordinates,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_date, esri_point, iter_features
from shared.species.english import species_from_common_name

LAYER = FeatureLayer(
    "https://arcgisweb.welland.ca/serverprodpub/rest/services/OpenData/"
    "OPENDATA_Active_Trees/MapServer/0",
    timeout=300,
)
OUT_FIELDS = "OBJECTID,Genus,Species,DiamDBH,DatePlant"

ID_PREFIX = "welland"

# Above this a centimetre DBH is a typing slip in this inventory (the largest
# real stems are 107 and 119 cm; the next values are 211, 277, ... 5,544).
MAX_DBH_CM = 200

# Dates put on established trees in one session, not plantings (see above).
STAMPED_PLANT_DATES = frozenset({date(2014, 9, 3), date(2014, 8, 27)})

# Typos in the common name, fixed before the lookup (normalised keys).
COMMON_NAME_TYPOS: dict[str, str] = {
    "maple - autmun blaze": "maple - autumn blaze",
    "maple - autumun blaze": "maple - autumn blaze",
    "maple - autumn blaaze": "maple - autumn blaze",
    "maple autumn blaze": "maple - autumn blaze",
    "maple - crinson king": "maple - crimson king",
    "maple - cirmson king": "maple - crimson king",
    "maple - campastre": "maple - hedge",
    "elm - japenese": "elm - japanese",
    "elm - jamanese": "elm - japanese",
    "lindin": "linden",
    "gingko": "ginkgo",
    "locust - imprial": "locust - imperial",
    "liquid amber": "gum - american sweet",
}

# This inventory's common names the shared table does not resolve, or folds
# to the taxon where the City recorded a named cultivar (normalised keys).
# The quoted cultivar is lifted into `cultivar` by `enforce_tree_schema`.
LOCAL_COMMON_NAMES: dict[str, str] = {
    "maple - crimson king": "Acer platanoides 'Crimson King'",
    "maple - autumn blaze": "Acer x freemanii 'Autumn Blaze'",
    "maple - autumn fantasy": "Acer x freemanii 'Autumn Fantasy'",
    "elm - valley forge": "Ulmus americana 'Valley Forge'",
    "elm - japanese": "Ulmus davidiana",
    "locust - skyline": "Gleditsia triacanthos 'Skyline'",
    "locust - imperial": "Gleditsia triacanthos 'Imperial'",
    "locust - sunburst": "Gleditsia triacanthos 'Sunburst'",
    "tilia": "Tilia",
}

# Genus + common name pairs where the genus is the wrong drop-down row and
# the common name is unambiguous: the common name's answer is published.
GENUS_SLIPS: frozenset[tuple[str, str]] = frozenset(
    {
        ("fraxinus", "ash - mountain"),
        ("castanea", "chestnut - horse"),
    }
)

# `Genus` values that are not a genus.
NOT_A_GENUS = {"", "other"}

DROPPED: dict[str, int] = {"no geometry": 0}
SPECIES_COUNTS: dict[str, int] = {
    "common name agrees with genus": 0,
    "genus only (common name unresolved)": 0,
    "common name only (no genus)": 0,
    "genus slip, common name wins": 0,
    "genus and common name contradict (Unknown)": 0,
    "neither recorded": 0,
}
DBH_COUNTS: dict[str, int] = {"zero": 0, "typing slip > 200 cm": 0}
DATE_COUNTS: dict[str, int] = {"stamped date nulled": 0}
CONTRADICTIONS: dict[tuple[str, str], int] = {}
UNRESOLVED: dict[str, int] = {}


def _norm(value) -> str:
    """`Maple -Hedge`, `Maple- Silver`, `MAple  - Autumn Blaze` -> `maple - hedge` ..."""
    text = " ".join(str(value or "").split()).casefold()
    text = re.sub(r"\s*-\s*", " - ", text)
    return COMMON_NAME_TYPOS.get(text, text)


def _genus_of(name: str | None) -> str | None:
    return name.split()[0].casefold() if name else None


def resolve_common(common: str) -> str | None:
    if not common:
        return None
    return LOCAL_COMMON_NAMES.get(common) or species_from_common_name(common)


def scientific_name(rec: dict) -> str | None:
    genus_raw = " ".join(str(rec.get("Genus") or "").split())
    common = _norm(rec.get("Species"))
    # A common name typed in the genus column (`Maple - Norway`) is not a genus.
    genus = genus_raw if re.fullmatch(r"[A-Za-z]+", genus_raw) else ""
    if genus.casefold() in NOT_A_GENUS:
        genus = ""
    genus = genus.capitalize()
    from_common = resolve_common(common)
    if common and from_common is None and common != "other":
        UNRESOLVED[common] = UNRESOLVED.get(common, 0) + 1

    if not genus:
        if from_common:
            SPECIES_COUNTS["common name only (no genus)"] += 1
        else:
            SPECIES_COUNTS["neither recorded"] += 1
        return from_common
    if from_common is None:
        SPECIES_COUNTS["genus only (common name unresolved)"] += 1
        return genus
    if _genus_of(from_common) == genus.casefold():
        SPECIES_COUNTS["common name agrees with genus"] += 1
        return from_common
    if (genus.casefold(), common) in GENUS_SLIPS:
        SPECIES_COUNTS["genus slip, common name wins"] += 1
        return from_common
    SPECIES_COUNTS["genus and common name contradict (Unknown)"] += 1
    key = (genus, common)
    CONTRADICTIONS[key] = CONTRADICTIONS.get(key, 0) + 1
    return None


def diameter_inches(value) -> float | None:
    if value is None:
        return None
    cm = float(value)
    if cm <= 0:
        DBH_COUNTS["zero"] += 1
        return None
    if cm > MAX_DBH_CM:
        DBH_COUNTS["typing slip > 200 cm"] += 1
        return None
    return cm_to_inches(cm)


def plant_date(value) -> date | None:
    planted = esri_ms_to_date(value)
    if planted in STAMPED_PLANT_DATES:
        DATE_COUNTS["stamped date nulled"] += 1
        return None
    return planted


def keep(feature: dict) -> bool:
    lat, _ = esri_point(feature.get("geometry"))
    if lat is None:
        DROPPED["no geometry"] += 1
        return False
    return True


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    planted: list[date | None] = []
    latitude: list[float] = []
    longitude: list[float] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        tree_id.append(coordinate_tree_id(ID_PREFIX, lon, lat))
        species.append(scientific_name(rec))
        planted.append(plant_date(rec.get("DatePlant")))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(diameter_inches(rec.get("DiamDBH")))

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CAWLD"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array(planted, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


def iter_pages():
    yield from iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    )


if __name__ == "__main__":
    table = stream_to_table(iter_pages(), transform, keep=keep, label="Welland OpenData")
    print(
        f"Welland: dropped {DROPPED}; species {SPECIES_COUNTS}; DBH {DBH_COUNTS}; "
        f"plant dates {DATE_COUNTS}; contradictions "
        f"{sorted(CONTRADICTIONS.items(), key=lambda kv: -kv[1])}; "
        f"unresolved common names {sorted(UNRESOLVED.items(), key=lambda kv: -kv[1])}",
        file=sys.stderr,
    )
    table = drop_stacked_coordinates(
        table, label="Welland", identical=("species", "diameter_at_breast_height")
    )
    table = table.filter(pc.is_valid(table["latitude"]))
    table = validate_coordinates(table, city="Welland", city_code="CAWLD")
    table = enforce_tree_schema(table, city="Welland", data_source="WELLAND_OPENDATA")
    emit(table)

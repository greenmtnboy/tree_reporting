#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Plano, Texas's inventory of City trees (parks, road medians, municipal sites).

Licence: no licence stated; displayed with attribution only, not licensed for
reuse.  The item's licence field is a disclaimer, not a grant: "This data and
information contained in it were developed exclusively for the City of Plano.
Any use or reliance on this data by anyone else is at that party's own risk
and without liability to the City of Plano, its officials or employees for any
discrepancies, errors, or variances which may exist."
(https://www.arcgis.com/home/item.html?id=a7e06ad57e2747738fb2b2117e23d68e).
It forbids nothing; it grants nothing either.

Source: "Shade Map_Trees" (ArcGIS item a7e06ad57e2747738fb2b2117e23d68e, owner
`dstiller@plano.gov_planogis`, the City of Plano's ArcGIS Online organisation
`planogis`), hosted layer `Shade_Map_WSL1/FeatureServer/0`: 43,581 points on
2026-11-23.  The item describes it as "the trees inventoried within Plano
parks, road medians, and municipal sites ... genus, name, canopy size, health
condition, etc.  Data provided by PlanIT Geo in June 2023"; credits
"Inventory - PlanIT Geo, Inc.  GIS Schema and QAQC - Tina Bashor".  It is the
point layer behind the City's 3D "Plano Shade Map", and the City publishes no
other tree inventory (its open-data services on maps.planogis.org have none;
the `Trees_PublicView` layer under the name "City of Plano" is Plano,
Illinois).  The scene-only columns (`Tree_Height_Int`, `Tree_Height_Random`,
`Tree_Rotation`, the 3D symbol's jittered height and rotation) are not read.

**Which rows are trees.**  Every row is a tree point; the layer has no
status or site-type column, so nothing is dropped for being a vacant site.
936 points added after the 2023 survey (OBJECTID 41,610 onwards) carry no
attribute at all -- no species, size or address -- and publish as `Unknown`
trees of unknown size.

**The id is the GlobalID** (`plano-{guid}`), non-null on every row; a
repeated GlobalID refuses the build.  The layer is edited in place (OBJECTIDs
1..44,029 with 448 gaps, `editingInfo` moving), so its GlobalIDs survive.

**Species is `Scientific_Name`**, Latin binomials with the trade's
abbreviations (`Lagerstroemia spp`, `Celtis species`, `Photinia xfraseri`,
`x Cupressocyparis leylandii`, a quoted cultivar `Pyrus Calleryana
'Bradford'`), which the shared hygiene reads; `SPECIES_FIXES` reads the ones
it cannot (`Broadleaf Deciduous Medium` is a size-class placeholder, not a
taxon; `× Chitalpa` is the nothogenus).  A row with no scientific name (1,100
or so with a common name only) is read from `Common_Name` through the shared
English table (`shared.species.english`; an unresolved name publishes
Unknown).  `Genus` and `Type` (`Oak`, `Evergreen`, ...) are not read: the
genus is in the binomial and `Type` is a symbol class for the 3D scene.

**DBH is `DBH`, whole inches** (median ~8, maximum 68; the PlanIT Geo
inventory's US unit).  0 and null are unmeasured (the shared guard nulls 0).

**`Canopy_Diameter` is a crown-spread class in feet** (`0-10'`, `10-20'`,
... `80-90'`), read as the class midpoint in metres into `crown_width_m` --
Ballarat's and Vienna's rule for a banded crown spread.  An empty value is
null; an unseen class counts as unreadable and more than 1% unreadable refuses
the build.  `Tree_Height` is a height class (`under 25'`, `25'-45'`, `over
45'`) and has no canonical column.  The layer records no planting date.
"""

import re
import sys
from collections import Counter
from datetime import date
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
    "https://services2.arcgis.com/1DyhsVa6rviDKn5t/arcgis/rest/services/Shade_Map_WSL1/FeatureServer/0",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,GlobalID,Scientific_Name,Common_Name,DBH,Canopy_Diameter"
ID_PREFIX = "plano"

# Refuse to publish when more than this share of non-empty values cannot be read.
MAX_UNPARSED_SHARE = 0.01

FEET_TO_M = 0.3048
# `Canopy_Diameter` class (`10-20'`) -> its bounds in feet.
CANOPY_CLASS = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*'\s*$")

# `Scientific_Name` values (lower-cased, whitespace collapsed) the shared
# hygiene reads wrongly -> the species value to publish.  None means "no
# Latin name": the common name is read instead.
SPECIES_FIXES: dict[str, str | None] = {
    # Size-class placeholders from the inventory's species list, not taxa.
    "broadleaf deciduous medium": None,
    "broadleaf deciduous small": None,
    # The nothogenus ×Chitalpa (Catalpa x Chilopsis); the shared hygiene drops
    # a leading hybrid mark with no epithet.  Penticton's rule.
    "× chitalpa": "Chitalpa",
    "x chitalpa": "Chitalpa",
}


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("‘", "'").replace("’", "'").split())
    # One cultivar is published with a mis-encoded closing quote
    # (`Pistacia x 'Red Push�`): close it properly.
    text = text.replace("�", "'").replace("”", "'").replace("“", "'")
    return text or None


class Counts:
    no_geometry = 0
    no_id = 0
    canopy_values = 0
    canopy_unparsed: Counter = Counter()
    from_common = 0
    unresolved_common: Counter = Counter()


COUNTS = Counts()
SEEN_IDS: set[str] = set()


def keep(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    guid = str(attrs.get("GlobalID") or "").strip().strip("{}").lower()
    if not guid:
        COUNTS.no_id += 1
        return False
    if guid in SEEN_IDS:
        raise RuntimeError(f"Plano ingest: GlobalID {guid} is repeated")
    SEEN_IDS.add(guid)
    lat, lon = esri_point(feature.get("geometry"))
    if lat is None or lon is None:
        COUNTS.no_geometry += 1
        return False
    return True


def species_value(scientific, common) -> str | None:
    """The species to publish: the Latin name, else the common name read
    through the shared English table."""
    text = clean(scientific)
    if text is not None:
        key = text.lower()
        if key not in SPECIES_FIXES:
            return text
        fixed = SPECIES_FIXES[key]
        if fixed is not None:
            return fixed
    name = clean(common)
    if name is None:
        return None
    species = species_from_common_name(name)
    if species:
        COUNTS.from_common += 1
        return species
    COUNTS.unresolved_common[name] += 1
    return None


def crown_width_m(raw) -> float | None:
    text = clean(raw)
    if text is None:
        return None
    COUNTS.canopy_values += 1
    match = CANOPY_CLASS.match(text)
    if match is None:
        COUNTS.canopy_unparsed[text] += 1
        return None
    low, high = int(match.group(1)), int(match.group(2))
    return round((low + high) / 2 * FEET_TO_M, 2)


def dbh_inches(raw) -> float | None:
    if raw is None:
        return None
    value = float(raw)
    return value if value > 0 else None


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
    crown: list[float | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        guid = str(attrs["GlobalID"]).strip().strip("{}").lower()
        tree_id.append(f"{ID_PREFIX}-{guid}")
        species.append(species_value(attrs.get("Scientific_Name"), attrs.get("Common_Name")))
        planted.append(None)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(dbh_inches(attrs.get("DBH")))
        crown.append(crown_width_m(attrs.get("Canopy_Diameter")))

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["USPLN"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "plant_date": pa.array(planted, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        "crown_width_m": pa.array(crown, type=pa.float64()),
    })


if __name__ == "__main__":
    table = stream_to_table(iter_chunks(), transform, keep=keep, label="Plano OpenData")
    print(
        f"Plano ingest: dropped {COUNTS.no_id} with no GlobalID, "
        f"{COUNTS.no_geometry} with no geometry",
        file=sys.stderr,
    )
    print(
        f"Plano ingest: {COUNTS.from_common:,} trees with no Latin name read from "
        f"their common name; {sum(COUNTS.unresolved_common.values()):,} common names "
        f"unresolved, top: {COUNTS.unresolved_common.most_common(20)}",
        file=sys.stderr,
    )
    unparsed = sum(COUNTS.canopy_unparsed.values())
    if unparsed:
        print(
            f"Plano ingest: {unparsed} Canopy_Diameter values unreadable: "
            f"{COUNTS.canopy_unparsed.most_common(10)}",
            file=sys.stderr,
        )
    if COUNTS.canopy_values and unparsed / COUNTS.canopy_values > MAX_UNPARSED_SHARE:
        raise RuntimeError(
            f"Plano ingest: {unparsed} of {COUNTS.canopy_values} Canopy_Diameter "
            "values unreadable; the canopy classes have changed"
        )
    table = validate_coordinates(table, city="Plano", city_code="USPLN")
    table = enforce_tree_schema(table, city="Plano", data_source="PLANO_OPENDATA")
    emit(table)

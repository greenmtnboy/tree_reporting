#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Grand Junction, Colorado's City tree inventory.

Source: "Trees" (ArcGIS item cef93c83233749eb9265914aca06b35c, owner
`chrisd@gjcity.org_GJcityGIS`, credited to "City of Grand Junction GIS";
"Data is maintained by Parks Forestry and the GIS department"), the City's
own map service `external25-gis.gjcity.org/arcgis/rest/services/Hub/City_Hub/
MapServer/1`: 16,206 rows on 2026-11-22, an enterprise geodatabase synced
from the City's Cartegraph asset system (`CartegraphID`, editor tracking
moving daily).  There is no FeatureServer twin; the MapServer pages by offset
(`maxRecordCount` 2000, `supportsPagination`).  Stored in EPSG:32612 and read
reprojected to WGS84 (`outSR=4326`).

Licence: the item's licence field reads "Creative Commons CC0 1.0 Universal
Public Domain Dedication"
(https://www.arcgis.com/home/item.html?id=cef93c83233749eb9265914aca06b35c).
Credited to the City of Grand Junction all the same.

**Which rows are trees.**  The layer has no site-type column; the vacant
planting sites are recorded as the common name `Vacant` (77 rows in Nov
2026).  A `Vacant` row with no scientific name is dropped with a logged
count; the handful that also carry a scientific name and a DBH class (a tree
planted on the site, the common name not updated) are kept as trees.  A row
with a `Retired` date (none in Nov 2026) is a removed tree and is dropped.
Private trees (`Owner`/`Designation`) are trees and are kept.

**The id is the GlobalID** (`grandjunction-{guid}`), non-null on every row;
a repeated GlobalID refuses the build.  OBJECTIDs run with gaps
(106,676..445,179) and `CartegraphID` is the sync key, so neither is needed.

**Species is `ScientificName`** (title-cased, `Gleditsia Tricanthos`); the
epithet is lower-cased and `Spp.` read as the genus.  `SPECIES_FIXES` reads
the values the general rule cannot: the size-class placeholders (`Broadleaf
Medium`, `Broadleaf Small`, `Conifer Evergreen Medium`) name no taxon,
`Ulmus Morton` is the Accolade elm cultivar, and two hybrids with no binomial
publish as their genus.  Single misspelt words are left to the shared
misspelling map.  A row with no scientific name, or only a placeholder, is
read from `CommonName` through the shared English table
(`shared.species.english`; `-` and unknown names publish Unknown).  Where a
Latin name is present it wins: the two columns were entered separately and
disagree on a few hundred rows.  A parse-failure guard refuses more than 1%
unreadable values.

**DBH is `TreeInspectionDBH`, a coded-value domain of 4-inch classes**
(`1" - 4" DBH` ... `45" - 48" DBH`), read as the class midpoint (Ballarat's
and Adelaide's rule: wrong per tree by up to two inches, right in
aggregate).  A code outside the domain refuses the build.  The layer has no
crown spread or height.

**Planting date** is `Installed`, an Esri date recorded for the trees the
City has planted since spring 2025 (311 rows); no placeholder stamp is in
use.  The stamps are local midnight written either as 00:00 UTC or as
06:00/07:00 UTC (Mountain time), so the UTC calendar date is the planting day.
"""

import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_date, esri_point, iter_features
from shared.species.english import species_from_common_name
from shared.ingest import (
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://external25-gis.gjcity.org/arcgis/rest/services/Hub/City_Hub/MapServer/1",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,GlobalID,ScientificName,CommonName,TreeInspectionDBH,Installed,Retired"
ID_PREFIX = "grandjunction"

# Refuse to publish when more than this share of named rows cannot be read.
MAX_UNPARSED_SHARE = 0.01

# `TreeInspectionDBH` class (`1" - 4" DBH`) -> its bounds in inches.
DBH_CLASS = re.compile(r'^\s*(\d+)"\s*-\s*(\d+)"\s*DBH\s*$', re.IGNORECASE)

# `ScientificName` values (lower-cased) the general rule reads wrongly ->
# (taxon, cultivar).  A None taxon means "no Latin name": the common name is
# read instead.
SPECIES_FIXES: dict[str, tuple[str | None, str | None]] = {
    # Size-class placeholders, not taxa.
    "broadleaf medium": (None, None),
    "broadleaf small": (None, None),
    "conifer evergreen medium": (None, None),
    # CommonName "Elm, Accolade": Ulmus 'Morton', a japonica x wilsoniana cross.
    "ulmus morton": ("Ulmus", "Morton"),
    # CommonName "Oak, Crimson Spire" on most rows; the Latin column names the
    # fastigiate English oak, and that is what is published.
    "quercus robur fastigiata": ("Quercus robur", "Fastigiata"),
    # CommonName "Magnolia, Galaxy": a liliiflora x sprengeri cross with no
    # binomial.
    "magnolia liliflora x sprengeri": ("Magnolia", None),
    # CommonName "Oak, Foothills": no such epithet in WCVP; the genus.
    "quercus maseyii": ("Quercus", None),
    "acer freemanii": ("Acer x freemanii", None),
}

HYBRID_MARKS = frozenset({"x", "×"})
GENUS_ONLY = frozenset({"spp", "spp.", "sp", "sp."})


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("‘", "'").replace("’", "'").split())
    return text or None


def parse_species(raw) -> tuple[str | None, str | None, bool]:
    """(taxon, cultivar, whether the value failed to parse).

    Examples:
        "Gleditsia Tricanthos"            -> ("Gleditsia tricanthos", None)
        "Malus Spp."                      -> ("Malus", None)
        "Magnolia X Soulangeana"          -> ("Magnolia x soulangeana", None)
        "Ulmus Morton"                    -> ("Ulmus", "Morton")
        "Broadleaf Medium"                -> (None, None)
    """
    text = clean(raw)
    if text is None:
        return None, None, False
    if text.lower() in SPECIES_FIXES:
        taxon, cultivar = SPECIES_FIXES[text.lower()]
        return taxon, cultivar, False
    words = text.split()
    if not all(w.replace("-", "").replace(".", "").isalpha() for w in words):
        return None, None, True
    genus = words[0][:1].upper() + words[0][1:].lower()
    rest = [w.lower() for w in words[1:]]
    if not rest or (len(rest) == 1 and rest[0] in GENUS_ONLY):
        return genus, None, False
    if rest[0] in HYBRID_MARKS:
        if len(rest) != 2:
            return None, None, True
        return f"{genus} x {rest[1]}", None, False
    if len(rest) != 1:
        return None, None, True
    return f"{genus} {rest[0]}", None, False


def dbh_inches(raw) -> float | None:
    text = clean(raw)
    if text is None:
        return None
    match = DBH_CLASS.match(text)
    if match is None:
        raise RuntimeError(
            f"Grand Junction ingest: unseen TreeInspectionDBH class {raw!r}; "
            "the DBH domain has changed"
        )
    low, high = int(match.group(1)), int(match.group(2))
    return (low + high) / 2


class Counts:
    no_geometry = 0
    no_id = 0
    vacant = 0
    retired = 0
    unparsed: Counter = Counter()
    named = 0
    from_common = 0
    unresolved_common: Counter = Counter()


COUNTS = Counts()
SEEN_IDS: set[str] = set()


def keep(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    if attrs.get("Retired") is not None:
        COUNTS.retired += 1
        return False
    if (clean(attrs.get("CommonName")) or "").lower() == "vacant" and not clean(
        attrs.get("ScientificName")
    ):
        COUNTS.vacant += 1
        return False
    guid = str(attrs.get("GlobalID") or "").strip().strip("{}").lower()
    if not guid:
        COUNTS.no_id += 1
        return False
    if guid in SEEN_IDS:
        raise RuntimeError(f"Grand Junction ingest: GlobalID {guid} is repeated")
    SEEN_IDS.add(guid)
    lat, lon = esri_point(feature.get("geometry"))
    if lat is None or lon is None:
        COUNTS.no_geometry += 1
        return False
    return True


def common_name_species(raw) -> str | None:
    """The species of a row with no Latin name, from its common name."""
    name = clean(raw)
    if name is None or name == "-":
        return None
    species = species_from_common_name(name)
    if species:
        COUNTS.from_common += 1
        return species
    COUNTS.unresolved_common[name] += 1
    return None


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
    cultivar: list[str | None] = []
    planted: list[date | None] = []
    latitude: list[float] = []
    longitude: list[float] = []
    dbh: list[float | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        raw = attrs.get("ScientificName")
        name, cv, failed = parse_species(raw)
        if clean(raw):
            COUNTS.named += 1
        if failed:
            COUNTS.unparsed[clean(raw)] += 1
        elif name is None:
            name = common_name_species(attrs.get("CommonName"))
        guid = str(attrs["GlobalID"]).strip().strip("{}").lower()
        tree_id.append(f"{ID_PREFIX}-{guid}")
        species.append(name)
        cultivar.append(cv)
        planted.append(esri_ms_to_date(attrs.get("Installed")))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(dbh_inches(attrs.get("TreeInspectionDBH")))

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["USGJT"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "cultivar": pa.array(cultivar, type=pa.string()),
        "plant_date": pa.array(planted, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


if __name__ == "__main__":
    table = stream_to_table(iter_chunks(), transform, keep=keep, label="Grand Junction OpenData")
    print(
        f"Grand Junction ingest: dropped {COUNTS.vacant} vacant sites, "
        f"{COUNTS.retired} retired trees, {COUNTS.no_id} with no GlobalID, "
        f"{COUNTS.no_geometry} with no geometry",
        file=sys.stderr,
    )
    print(
        f"Grand Junction ingest: {COUNTS.from_common:,} trees with no Latin name read "
        f"from their common name; {sum(COUNTS.unresolved_common.values()):,} common "
        f"names unresolved, top: {COUNTS.unresolved_common.most_common(20)}",
        file=sys.stderr,
    )
    unparsed = sum(COUNTS.unparsed.values())
    if unparsed:
        print(
            f"Grand Junction ingest: {unparsed} species values unreadable: "
            f"{COUNTS.unparsed.most_common(10)}",
            file=sys.stderr,
        )
    if COUNTS.named and unparsed / COUNTS.named > MAX_UNPARSED_SHARE:
        raise RuntimeError(
            f"Grand Junction ingest: {unparsed} of {COUNTS.named} species values "
            "unreadable; the ScientificName field's format has changed"
        )
    table = validate_coordinates(table, city="Grand Junction", city_code="USGJT")
    table = enforce_tree_schema(
        table, city="Grand Junction", data_source="GRAND_JUNCTION_OPENDATA"
    )
    emit(table)

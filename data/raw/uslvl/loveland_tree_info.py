#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Loveland, Colorado's City tree inventory, from the City's Cityworks GIS server.

LICENCE: no licence stated; displayed with attribution only, not licensed for
reuse.  The portal item's `licenseInfo`, `accessInformation` and description
are empty (https://www.arcgis.com/home/item.html?id=0f66e47e8c7a4e60804ec871d5827ff1),
so is the map service's own item info
(https://cityworks.cityofloveland.org/arcgis/rest/services/ParksandRec/Trees/MapServer/info/iteminfo),
and the City's GIS hub (https://loveland-gis-thelogicsystem.hub.arcgis.com)
states no terms.

Source: "City of Loveland Trees" (ArcGIS item 0f66e47e8c7a4e60804ec871d5827ff1,
owner `COLGIS2` of the City's organisation "City of Loveland GIS - The LOGIC
System"; snippet "Trees in and around City of Loveland, Colorado"), the
Parks and Recreation `Trees` MapServer layer 0 on the City's Cityworks
server: 26,561 rows on 2026-11-27, street trees (`TreeType = Street Tree`,
17,396), park trees (7,733), HOA, donation and EAB-treatment trees.  It is
the live Cityworks asset layer: `InspectDate` moves daily (a bulk re-stamp
on 2026-06-26, a few hundred edits a month since).  Geometry is stored in
Colorado North State Plane feet (EPSG 2876) and read in WGS84 (`outSR=4326`);
`ESRIGNSS_LATITUDE`/`ESRIGNSS_LONGITUDE` are the field crew's GNSS fix and
are missing on 3,781 rows, so the geometry is read.

**The id is the GlobalID** (`loveland-{guid}`), non-null and unique over the
whole layer (26,561 distinct, checked 2026-11-27); a repeat refuses the
build.  `FacilityID` (the Cityworks asset number) repeats on 17 values, and
OBJECTIDs run 1..64,452 with gaps.

**Which rows are trees.**  `Status` (a coded-value domain): `Alive`, `Dead`,
`Proposed Removal` and `Habitat Snag` are trees standing there and stay, as
do the 1,738 street-tree rows with an empty status (every one names a
species and a condition).  `Stump`, `Proposed Planting` (a planting site)
and `Abandoned Tree` are dropped: an abandoned asset is a retired record,
and their comments say so ("Tree removed on 6.14.2023", "Stump 25\"",
"hole in the ground", "Private Property and maintained by homeowner").  An
unseen status refuses the build.

**Species is `LatinName`** (a coded-value domain of 241 botanical names,
some with the cultivar quoted: `Acer tataricum 'GarAnn'`, `Ulmus x
'Accolade'`), read through the shared sanitiser, which turns `Malus
species` / `Malus x` into the genus and folds synonyms and misspellings.
When it is empty the row's `Genus` (Latin) stands in.  `NAME_FIXES` reads
the domain's few values that are not a name as written: `Arborvitae` (a
common name, *Thuja*) and three unquoted cultivars.  `CommonName` is not
mapped.  A value that does not read as a Latin name counts as unreadable;
above 1% the build is refused.

**Cultivar** is the one quoted in `LatinName`, else the `Cultivar` column
(a coded-value domain; `CULTIVAR_FIXES` corrects its typos and casing).

**DBH is `DBH`, inches** (median 7, 99th percentile 36, a few cottonwoods
to 78; tenths on about a fifth of rows); 0 is unmeasured and nulled.

**Crown spread is `MaxWidthft`, feet**, published in metres; 0 is
unmeasured.  `TreeHeight` (feet) has no column in the schema.

**Planting date is `PlantDate`**, mostly the first of a month (an estimated
season, kept); 1899-12-30, the zero date of a spreadsheet, is a placeholder
and is nulled.
"""

import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (  # noqa: E402
    FeatureLayer,
    esri_ms_to_date,
    esri_point,
    iter_features,
)
from shared.ingest import (  # noqa: E402
    emit,
    enforce_tree_schema,
    extract_cultivar,
    stream_to_table,
    validate_coordinates,
)

SOURCE_URL = (
    "https://cityworks.cityofloveland.org/arcgis/rest/services/"
    "ParksandRec/Trees/MapServer/0"
)
LAYER = FeatureLayer(SOURCE_URL, timeout=180)
OUT_FIELDS = "OBJECTID,GlobalID,Status,LatinName,Genus,Cultivar,PlantDate,DBH,MaxWidthft"
ID_PREFIX = "loveland"

TREE_STATUSES = frozenset({"alive", "dead", "proposed removal", "habitat snag", ""})
NOT_TREE_STATUSES = frozenset({"stump", "proposed planting", "abandoned tree"})

NO_NAME = frozenset({"", "n/a"})

# `LatinName` values (lower-cased) that are not a name as written ->
# (species, cultivar).
NAME_FIXES: dict[str, tuple[str, str | None]] = {
    "arborvitae": ("Thuja", None),
    # 'Pacific Sunset' maple, A. truncatum x A. platanoides 'Warrenred':
    # a hybrid with no binomial resolves to its genus.
    "acer truncatum x warrenred": ("Acer", "Warrenred"),
    # Red horse-chestnut 'Fort McNair'.
    "aesculus x fort mcnair": ("Aesculus x carnea", "Fort McNair"),
    "prunus domestica mount royal": ("Prunus domestica", "Mount Royal"),
}

# `Genus` values that are not a genus.
GENUS_FIXES: dict[str, str | None] = {"arborvitae": "Thuja", "n/a": None}

# `Cultivar` domain values as the nursery trade writes them.
CULTIVAR_FIXES: dict[str, str] = {
    "Spring snow": "Spring Snow",
    "chanticleer": "Chanticleer",
    "Nothern Spy": "Northern Spy",
    "Comice": "Doyenne du Comice",
}

PLACEHOLDER_DATES = frozenset({date(1899, 12, 30)})

MAX_UNPARSED_SHARE = 0.01
FEET_TO_METRES = 0.3048

# Optional hybrid mark, genus, then words (epithet, hybrid mark, `species`),
# then an optional quoted cultivar.
LATIN = re.compile(r"^(X )?[A-Z][a-z]+( [xX]| [a-z][a-z.-]*| [A-Z][a-z]+)*( ['`\"].*)?$")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("\xa0", " ").split())
    return text or None


class Counts:
    status: Counter = Counter()
    no_id = 0
    no_geometry = 0
    named = 0
    from_genus = 0
    unnamed = 0
    unparsed: Counter = Counter()
    placeholder_dates = 0


COUNTS = Counts()
SEEN_IDS: set[str] = set()


def keep(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    status = (clean(attrs.get("Status")) or "").lower()
    if status not in TREE_STATUSES:
        if status not in NOT_TREE_STATUSES:
            raise RuntimeError(
                f"Loveland ingest: unseen Status {attrs.get('Status')!r}; "
                "the layer's status domain has changed"
            )
        COUNTS.status[status] += 1
        return False
    guid = str(attrs.get("GlobalID") or "").strip().strip("{}").lower()
    if not guid:
        COUNTS.no_id += 1
        return False
    if guid in SEEN_IDS:
        raise RuntimeError(f"Loveland ingest: GlobalID {guid} is repeated")
    SEEN_IDS.add(guid)
    lat, lon = esri_point(feature.get("geometry"))
    if lat is None or lon is None:
        COUNTS.no_geometry += 1
        return False
    return True


def parse_species(latin_raw, genus_raw, cultivar_raw) -> tuple[str | None, str | None]:
    """(species, cultivar) from `LatinName`, `Genus` and `Cultivar`.

    Examples:
        ("Acer tataricum 'GarAnn'", "Acer", "Hot Wings") -> ("Acer tataricum 'GarAnn'", "GarAnn")
        ("Malus species", "Malus", "Spring snow")        -> ("Malus species", "Spring Snow")
        ("Arborvitae", "Arborvitae", " ")                -> ("Thuja", None)
        (" ", "Populus", " ")                            -> ("Populus", None)
    """
    latin = clean(latin_raw)
    column_cultivar = clean(cultivar_raw)
    if column_cultivar is not None:
        column_cultivar = CULTIVAR_FIXES.get(column_cultivar, column_cultivar)
    if latin is not None and latin.lower() not in NO_NAME:
        COUNTS.named += 1
        fix = NAME_FIXES.get(latin.lower())
        if fix is not None:
            return fix[0], fix[1] or column_cultivar
        if not LATIN.match(latin):
            COUNTS.unparsed[latin] += 1
            return None, None
        latin = latin.replace("`", "'")
        return latin, extract_cultivar(latin) or column_cultivar
    genus = clean(genus_raw)
    if genus is not None:
        key = genus.lower()
        resolved = GENUS_FIXES.get(key, genus if genus.isalpha() else None)
        if resolved:
            COUNTS.from_genus += 1
            return resolved, column_cultivar
    COUNTS.unnamed += 1
    return None, None


def plant_date(value) -> date | None:
    day = esri_ms_to_date(value)
    if day in PLACEHOLDER_DATES:
        COUNTS.placeholder_dates += 1
        return None
    return day


def positive(value) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if number > 0 else None


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
    crown: list[float | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        guid = str(attrs["GlobalID"]).strip().strip("{}").lower()
        name, selection = parse_species(
            attrs.get("LatinName"), attrs.get("Genus"), attrs.get("Cultivar")
        )
        tree_id.append(f"{ID_PREFIX}-{guid}")
        species.append(name)
        cultivar.append(selection if name else None)
        planted.append(plant_date(attrs.get("PlantDate")))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(positive(attrs.get("DBH")))
        width_ft = positive(attrs.get("MaxWidthft"))
        crown.append(width_ft * FEET_TO_METRES if width_ft is not None else None)

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["USLVL"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "cultivar": pa.array(cultivar, type=pa.string()),
        "plant_date": pa.array(planted, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        "crown_width_m": pa.array(crown, type=pa.float64()),
    })


if __name__ == "__main__":
    table = stream_to_table(iter_chunks(), transform, keep=keep, label="Loveland OpenData")
    dropped = ", ".join(f"{n:,} {why}" for why, n in COUNTS.status.most_common())
    print(
        f"Loveland ingest: dropped {sum(COUNTS.status.values()):,} rows that are "
        f"not trees ({dropped}), {COUNTS.no_id} with no GlobalID, "
        f"{COUNTS.no_geometry} with no geometry; {COUNTS.from_genus} named from "
        f"Genus, {COUNTS.unnamed} with no name, {COUNTS.placeholder_dates} "
        "placeholder plant dates nulled",
        file=sys.stderr,
    )
    unparsed = sum(COUNTS.unparsed.values())
    if unparsed:
        print(
            f"Loveland ingest: {unparsed} species values unreadable: "
            f"{COUNTS.unparsed.most_common(10)}",
            file=sys.stderr,
        )
    if COUNTS.named and unparsed / COUNTS.named > MAX_UNPARSED_SHARE:
        raise RuntimeError(
            f"Loveland ingest: {unparsed} of {COUNTS.named} species values "
            "unreadable; the LatinName field's format has changed"
        )
    table = validate_coordinates(table, city="Loveland", city_code="USLVL")
    table = enforce_tree_schema(
        table, city="Loveland", data_source="LOVELAND_OPENDATA"
    )
    emit(table)

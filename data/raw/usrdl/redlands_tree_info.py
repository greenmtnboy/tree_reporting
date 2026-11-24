#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Redlands, California's City street-tree inventory.

Source: "Street Trees" (ArcGIS item 9f9d4ad74a0d44fba8bf27c829e810f2, owner
`cityofredlands`, credited to "City of Redlands": "City owned street trees
and tree plots, including park trees"), the hosted layer
`services.arcgis.com/FLM8UAw9y5MmuVTV/.../Street_Trees/FeatureServer/0`:
54,150 rows on 2026-11-23.  It is the City's 2018 contractor inventory
(`DATEMODIFI` 7/30/2018 on 53,661 rows, the last in February 2019), last
edited in September 2023; the older `Trees` service on the same server
(53,870 rows, last edited January 2018) is its predecessor and is not read.
Stored in EPSG:2229 (California State Plane V, feet) and read reprojected to
WGS84 (`outSR=4326`).

Licence: none stated -- displayed with attribution only, not licensed for
reuse.  The item's licence field and the GeoHub's Disclaimer page
(https://datahub-coredlands.hub.arcgis.com/pages/disclaimer) say only that
"The City of Redlands through its GIS Division is making data available
through GeoHub as a public service, on an 'AS-IS' basis, and for
informational purposes only", that it is "subject to the Terms of Use
developed by the GIS Division" (not published), and give a recommended
citation.  That is a disclaimer, not a licence; nothing forbids reuse.

**Which rows are trees.**  `BOTANICALN` records the empty sites as
`Vacant site` (10,262) and `Stump` (1,474); both are dropped, as is one named
tree whose note (`OPTIONAL5`) says `Stump`.  The layer has no removed/retired
column: the trees the City has taken out are recorded as stumps or vacant
sites, which the rule above already drops.  Dead standing trees (`TREECONDIT
= Dead`, and the 13 rows named `Dead Tree`) are trees and are kept.  279 rows
added after the survey (`INVENTORYI = 0`) carry a position and an address but
no species, condition or (mostly) size; they are kept as trees of unrecorded
species.

**The id is the GlobalID** (`redlands-{guid}`), non-null and unique over the
layer (checked 2026-11-23); a repeated GlobalID refuses the build.
`FacilityId` is the OBJECTID as text and `INVENTORYI` the contractor's id (0
on the 279 late rows), so neither is better.

**Species is `BOTANICALN`**, a scientific name throughout (the common name in
`COMMONNAME` is not read).  A quoted cultivar is split into `cultivar`,
except the flower or leaf colours the inventory writes in the same quotes
(`Lagerstroemia indica 'Pink'`, `Acer palmatum 'Red'`), which name no
cultivar.  `SPECIES_FIXES` reads the values the general rule cannot; single
misspelt words are left to the shared misspelling map.  A value that is not a
Latin name refuses the build past 1%.

**DBH is `ACTUALDBH`, inches** (1..96; the values cluster on 3, 6, 34 and 35,
which look like class fills from the survey's `DBH` classes `0-6` and `31+`,
but are published as recorded).  Where it is blank the `DBH` class midpoint
is used (`07-12` -> 9.5); `31+` is open-ended and publishes no DBH.  More
than 1% unreadable values refuses the build.  `HEIGHT` is a class in feet
and there is no crown spread or planting date.
"""

import re
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    emit,
    enforce_tree_schema,
    extract_cultivar,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services.arcgis.com/FLM8UAw9y5MmuVTV/arcgis/rest/services/Street_Trees/FeatureServer/0",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,GlobalID,BOTANICALN,DBH,ACTUALDBH,OPTIONAL5"
ID_PREFIX = "redlands"

# Refuse to publish when more than this share of values cannot be read.
MAX_UNPARSED_SHARE = 0.01

# `BOTANICALN` values (lower-cased) that are not trees.
NOT_TREES = frozenset({"vacant site", "stump"})
# `OPTIONAL5` notes that say the site holds no tree.
NOT_TREE_NOTES = frozenset({"stump", "vacant", "vacant site"})

# `BOTANICALN` values (lower-cased, quotes included) the general rule reads
# wrongly -> (taxon, cultivar).
SPECIES_FIXES: dict[str, tuple[str | None, str | None]] = {
    "malus spp. & cv.": ("Malus", None),
    # Hybrid marks with no epithet, a cultivar after them.
    "citrus x 'tangelo'": ("Citrus", "Tangelo"),
    "parkinsonia x 'desert museum'": ("Parkinsonia", "Desert Museum"),
    "lagerstroemia indica x fauriei 'zuni'": ("Lagerstroemia", "Zuni"),
    # "Catalpa hybrida" is the trade name of C. x erubescens.
    "catalpa hybrida 'purpurea'": ("Catalpa x erubescens", "Purpurea"),
    "unidentified spp.": (None, None),
}

# Quoted values that are a colour, not a cultivar.
COLOUR_NOTES = frozenset({"pink", "purple", "red", "white", "green"})

DBH_CLASS = re.compile(r"^\s*0?(\d+)\s*-\s*0?(\d+)\s*$")
OPEN_CLASSES = frozenset({"31+"})
NO_CLASS = frozenset({"", "---"})


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("‘", "'").replace("’", "'").split())
    return text or None


def parse_species(raw) -> tuple[str | None, str | None, bool]:
    """(taxon, cultivar, whether the value failed to parse).

    Examples:
        "Quercus ilex"                    -> ("Quercus ilex", None)
        "Platanus X hispanica"            -> ("Platanus x hispanica", None)
        "Ginkgo biloba 'Autumn Gold'"     -> ("Ginkgo biloba", "Autumn Gold")
        "Lagerstroemia indica 'Pink'"     -> ("Lagerstroemia indica", None)
        "Rhaphiolepis 'Majestic Beauty'"  -> ("Rhaphiolepis", "Majestic Beauty")
        "Acacia spp."                     -> ("Acacia spp.", None)
    """
    text = clean(raw)
    if text is None:
        return None, None, False
    if text.lower() in SPECIES_FIXES:
        taxon, cultivar = SPECIES_FIXES[text.lower()]
        return taxon, cultivar, False
    cultivar = extract_cultivar(text)
    if cultivar is not None and cultivar.lower() in COLOUR_NOTES:
        cultivar = None
    name = text.split("'")[0].strip()
    words = name.split()
    if not words or not all(w.replace("-", "").replace(".", "").isalpha() for w in words):
        return None, None, True
    return normalize_species(name), cultivar, False


def dbh_inches(actual, dbh_class) -> tuple[float | None, bool]:
    """(DBH in inches, whether a value failed to parse)."""
    text = clean(actual)
    if text is not None:
        try:
            value = float(text)
        except ValueError:
            return None, True
        return (value if value > 0 else None), False
    cls = clean(dbh_class) or ""
    if cls in NO_CLASS or cls in OPEN_CLASSES:
        return None, False
    match = DBH_CLASS.match(cls)
    if match is None:
        raise RuntimeError(
            f"Redlands ingest: unseen DBH class {dbh_class!r}; the DBH classes have changed"
        )
    return (int(match.group(1)) + int(match.group(2))) / 2, False


class Counts:
    vacant = 0
    stump = 0
    stump_note = 0
    no_id = 0
    no_geometry = 0
    named = 0
    unparsed: Counter = Counter()
    dbh_values = 0
    dbh_unparsed: Counter = Counter()
    from_class = 0


COUNTS = Counts()
SEEN_IDS: set[str] = set()


def keep(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    name = (clean(attrs.get("BOTANICALN")) or "").lower()
    if name in NOT_TREES:
        if name == "stump":
            COUNTS.stump += 1
        else:
            COUNTS.vacant += 1
        return False
    if (clean(attrs.get("OPTIONAL5")) or "").lower() in NOT_TREE_NOTES:
        COUNTS.stump_note += 1
        return False
    guid = str(attrs.get("GlobalID") or "").strip().strip("{}").lower()
    if not guid:
        COUNTS.no_id += 1
        return False
    if guid in SEEN_IDS:
        raise RuntimeError(f"Redlands ingest: GlobalID {guid} is repeated")
    SEEN_IDS.add(guid)
    lat, lon = esri_point(feature.get("geometry"))
    if lat is None or lon is None:
        COUNTS.no_geometry += 1
        return False
    return True


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
    latitude: list[float] = []
    longitude: list[float] = []
    dbh: list[float | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        raw = attrs.get("BOTANICALN")
        name, cv, failed = parse_species(raw)
        if clean(raw):
            COUNTS.named += 1
        if failed:
            COUNTS.unparsed[clean(raw)] += 1
        diameter, dbh_failed = dbh_inches(attrs.get("ACTUALDBH"), attrs.get("DBH"))
        if clean(attrs.get("ACTUALDBH")):
            COUNTS.dbh_values += 1
        elif diameter is not None:
            COUNTS.from_class += 1
        if dbh_failed:
            COUNTS.dbh_unparsed[clean(attrs.get("ACTUALDBH"))] += 1
        guid = str(attrs["GlobalID"]).strip().strip("{}").lower()
        tree_id.append(f"{ID_PREFIX}-{guid}")
        species.append(name)
        cultivar.append(cv)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(diameter)

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["USRDL"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "cultivar": pa.array(cultivar, type=pa.string()),
        "plant_date": pa.array([None] * n, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


def check_parse_failures() -> None:
    unparsed = sum(COUNTS.unparsed.values())
    if unparsed:
        print(
            f"Redlands ingest: {unparsed} species values unreadable: "
            f"{COUNTS.unparsed.most_common(10)}",
            file=sys.stderr,
        )
    if COUNTS.named and unparsed / COUNTS.named > MAX_UNPARSED_SHARE:
        raise RuntimeError(
            f"Redlands ingest: {unparsed} of {COUNTS.named} species values "
            "unreadable; the BOTANICALN field's format has changed"
        )
    bad_dbh = sum(COUNTS.dbh_unparsed.values())
    if bad_dbh:
        print(
            f"Redlands ingest: {bad_dbh} ACTUALDBH values unreadable: "
            f"{COUNTS.dbh_unparsed.most_common(10)}",
            file=sys.stderr,
        )
    if COUNTS.dbh_values and bad_dbh / COUNTS.dbh_values > MAX_UNPARSED_SHARE:
        raise RuntimeError(
            f"Redlands ingest: {bad_dbh} of {COUNTS.dbh_values} ACTUALDBH values "
            "unreadable; the field's format has changed"
        )


if __name__ == "__main__":
    table = stream_to_table(iter_chunks(), transform, keep=keep, label="Redlands OpenData")
    print(
        f"Redlands ingest: dropped {COUNTS.vacant} vacant sites, {COUNTS.stump} stumps, "
        f"{COUNTS.stump_note} rows whose note says stump/vacant, {COUNTS.no_id} with no "
        f"GlobalID, {COUNTS.no_geometry} with no geometry; {COUNTS.from_class} DBH "
        "values read from the class midpoint",
        file=sys.stderr,
    )
    check_parse_failures()
    table = validate_coordinates(table, city="Redlands", city_code="USRDL")
    table = enforce_tree_schema(
        table, city="Redlands", data_source="REDLANDS_OPENDATA"
    )
    emit(table)

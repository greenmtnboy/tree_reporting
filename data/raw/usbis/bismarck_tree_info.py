#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Bismarck, North Dakota's City tree inventory.

LICENCE: no licence stated; displayed with attribution only, not licensed for
reuse.  The item's `licenseInfo` is empty, and the City's open-data Hub
("City of Bismarck Open Data", https://public-bismarck.opendata.arcgis.com,
which does not list the trees) states only a disclaimer under "Terms of Use":
"The City of Bismarck does not guarantee the accuracy, current status, or
completeness of the material contained herein ...".

Source: "Trees_public" (ArcGIS item c84565e8695d41fbbde0973470cefab6, owner
`BismarckGIS`, the City of Bismarck's GIS Division organisation): "A public
view of trees for use in the Our Urban Forest site and Tree Viewer app", a
hosted view `Trees_public_.../FeatureServer/0` of the Forestry division's
ArcGIS Solutions tree inventory (tags: Urban Forestry, Public Works, Asset
Management).  30,359 rows on 2026-11-22, edited in place (`editingInfo`
moves with the crews' edits).  The org's older `Bismarck_Trees_PublicView`
(48,659 rows of tree, planting, removal and stump sites, State Plane, last
edited November 2023) is the inventory this one replaced and is not read;
`Consultant_Trees` is another view of the same layer (29,100 rows).

**Which rows are trees.**  `spacestatus` (a coded-value domain): `Planted`
is a standing tree; `Retired` (a removed tree, its `retiredate` set on about
a fifth of them), `Stump`, `Available` (an empty planting space) and `Other`
(four rows, each a 1-2 inch tree whose space is in some other state) are
dropped with a logged count.  An unseen status refuses the build.

**The id is the GlobalID** (`bismarck-{guid}`), non-null and unique on every
row (the layer's `globalIdField`); `assetid` ("Site ID") is also non-null
but is the planting site's, shared by the successive trees in it.

**Species is `genus` + `species`** (coded-value domains of capitalised
words, `Fraxinus` / `Pennsylvanica`; the epithet column also holds values
outside its domain, `Freemanii`, `Mandshurica`).  `Other`, `Unknown` and an
empty epithet publish the genus alone.  A few rows pair a genus with another
genus's epithet (`Ulmus` / `Ginnala`, `Alnus` / `Alnus`): `MISMATCHED` reads
those as the genus, or as nothing where the common name names another tree.
Hybrids written without their mark (`Acer freemanii`, `Quercus bimundorum`)
are left to the shared misspelling map.  `Cultivar` is its own coded column.
The portal's `commonname` is never mapped.  A parse-failure guard refuses
more than 1% unreadable values.

**DBH is `diameter`, inches** (standing trees: mean ~10.5, max 86); `0` is
the unmeasured placeholder and is nulled.  `height` (feet) has no canonical
column, and the layer records no crown spread.

**Planting date** is `installdate`, a date field written as 1 June of the
planting year.  1900 is the unknown-date placeholder (70% of standing trees)
and is nulled, as are dates before the city's founding (1872) and after
today.
"""

import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_date, esri_point, iter_features
from shared.ingest import (
    emit,
    enforce_tree_schema,
    normalize_species_parts,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services1.arcgis.com/XxHmL09eFqJWI0gE/arcgis/rest/services/"
    "Trees_public_3694a69174e146b3b4b24d82a90c198a/FeatureServer/0",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,globalid,spacestatus,genus,species,Cultivar,diameter,installdate"
ID_PREFIX = "bismarck"

TREE_STATUSES = frozenset({"planted"})
NOT_A_TREE_STATUSES = frozenset({"retired", "stump", "available", "other"})

# Epithet values that name no species: the genus alone.
GENUS_ONLY_EPITHETS = frozenset({"", "other", "unknown"})

# (genus, epithet) pairs that are not a taxon, lower-cased -> what is
# published.  Each is an epithet from another genus's list; the layer's
# `commonname` says which half to trust.
MISMATCHED: dict[tuple[str, str], str | None] = {
    ("alnus", "alnus"): "Alnus",  # common name "Alder"
    ("ulmus", "ginnala"): "Ulmus",  # "Elm"
    ("acer", "babylonica"): "Acer",  # "Maple"
    ("acer", "cordata"): "Acer",  # "Maple"
    ("sorbus", "strobus"): "Sorbus",  # "Mountain-Ash"
    ("alnus", "americana"): None,  # common name "Elm": no telling which
}

# The genus domain's own misspelling.
GENUS_FIXES = {"gingko": "Ginkgo"}

# Refuse to publish when more than this share of named rows cannot be read.
MAX_UNPARSED_SHARE = 0.01

# Bismarck was founded in 1872.
EARLIEST_PLANT_YEAR = 1872
# The year the inventory stamps on a tree whose planting it does not know:
# 1900-06-01 on 17,254 of 24,711 standing trees in Nov 2026 (every other
# year is also written as 1 June, but none holds more than ~830 trees).
PLACEHOLDER_PLANT_YEAR = 1900


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


class Counts:
    no_geometry = 0
    no_id = 0
    not_a_tree: Counter = Counter()
    unparsed: Counter = Counter()
    named = 0
    mismatched = 0
    nulled_dates: Counter = Counter()


COUNTS = Counts()


def keep(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    status = (clean(attrs.get("spacestatus")) or "").lower()
    if status not in TREE_STATUSES:
        if status not in NOT_A_TREE_STATUSES:
            raise RuntimeError(
                f"Bismarck ingest: unseen spacestatus {attrs.get('spacestatus')!r}; "
                "decide whether it is a standing tree"
            )
        COUNTS.not_a_tree[status] += 1
        return False
    if not str(attrs.get("globalid") or "").strip("{} "):
        COUNTS.no_id += 1
        return False
    lat, lon = esri_point(feature.get("geometry"))
    if lat is None or lon is None:
        COUNTS.no_geometry += 1
        return False
    return True


def parse_species(raw_genus, raw_epithet) -> tuple[str | None, bool]:
    """(scientific name, whether the value failed to parse).

    Examples:
        ("Fraxinus", "Pennsylvanica") -> ("Fraxinus pennsylvanica", False)
        ("Malus", "")                 -> ("Malus", False)
        ("Ulmus", "Unknown")          -> ("Ulmus", False)
        ("Ulmus", "Ginnala")          -> ("Ulmus", False)
        (None, None)                  -> (None, False)
    """
    genus = clean(raw_genus)
    epithet = clean(raw_epithet) or ""
    if genus is None:
        return None, False
    if not genus.isalpha() or (epithet and not epithet.replace("-", "").isalpha()):
        return None, True
    genus = GENUS_FIXES.get(genus.lower(), genus)
    if epithet.lower() in GENUS_ONLY_EPITHETS:
        return normalize_species_parts(genus, None), False
    pair = (genus.lower(), epithet.lower())
    if pair in MISMATCHED:
        COUNTS.mismatched += 1
        return MISMATCHED[pair], False
    return normalize_species_parts(genus, epithet), False


def plant_date(raw) -> date | None:
    value = esri_ms_to_date(raw)
    if value is None:
        return None
    if value.year == PLACEHOLDER_PLANT_YEAR:
        COUNTS.nulled_dates["1900 placeholder"] += 1
        return None
    if value.year < EARLIEST_PLANT_YEAR:
        COUNTS.nulled_dates["before founding"] += 1
        return None
    if value > date.today():
        COUNTS.nulled_dates["future"] += 1
        return None
    return value


def positive(raw) -> float | None:
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
    cultivar: list[str | None] = []
    planted: list[date | None] = []
    latitude: list[float] = []
    longitude: list[float] = []
    dbh: list[float | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        name, failed = parse_species(attrs.get("genus"), attrs.get("species"))
        if clean(attrs.get("genus")):
            COUNTS.named += 1
        if failed:
            COUNTS.unparsed[(attrs.get("genus"), attrs.get("species"))] += 1
        guid = str(attrs["globalid"]).strip().strip("{}").lower()
        tree_id.append(f"{ID_PREFIX}-{guid}")
        species.append(name)
        cultivar.append(clean(attrs.get("Cultivar")) if name else None)
        planted.append(plant_date(attrs.get("installdate")))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(positive(attrs.get("diameter")))

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["USBIS"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "cultivar": pa.array(cultivar, type=pa.string()),
        "plant_date": pa.array(planted, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


if __name__ == "__main__":
    table = stream_to_table(iter_chunks(), transform, keep=keep, label="Bismarck OpenData")
    dropped = ", ".join(f"{n:,} {status}" for status, n in COUNTS.not_a_tree.most_common())
    print(
        f"Bismarck ingest: dropped {sum(COUNTS.not_a_tree.values()):,} rows that are "
        f"not standing trees ({dropped}), {COUNTS.no_id} with no GlobalID, "
        f"{COUNTS.no_geometry} with no geometry; nulled plant dates: "
        f"{dict(COUNTS.nulled_dates)}; {COUNTS.mismatched} genus/epithet "
        "mismatches read as the genus",
        file=sys.stderr,
    )
    unparsed = sum(COUNTS.unparsed.values())
    if unparsed:
        print(
            f"Bismarck ingest: {unparsed} species values unreadable: "
            f"{COUNTS.unparsed.most_common(10)}",
            file=sys.stderr,
        )
    if COUNTS.named and unparsed / COUNTS.named > MAX_UNPARSED_SHARE:
        raise RuntimeError(
            f"Bismarck ingest: {unparsed} of {COUNTS.named} species values "
            "unreadable; the genus/species fields' format has changed"
        )
    table = validate_coordinates(table, city="Bismarck", city_code="USBIS")
    table = enforce_tree_schema(
        table, city="Bismarck", data_source="BISMARCK_OPENDATA"
    )
    emit(table)

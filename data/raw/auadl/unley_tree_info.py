#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""City of Unley street and reserve trees, from the council's ArcGIS Online.

Source: "Street Trees" (item 60272d3be3d34d7598a05f495fe347be, owner
`UnleyOpenData`, the City of Unley's open-data account), the hosted layer
`Trees/FeatureServer/0`.  **No licence is stated on the item** (its licence
field is empty) and the council's open-data hub (opendata.unley.sa.gov.au)
states none either; the council's Open Data Policy (2023) commits to
publishing data "available for reuse such as those published under a
Creative Commons Attribution Licence" but names no licence for this layer.
So it is displayed with attribution only, not licensed for reuse.

Unley is the inner-southern council between the City of Adelaide's south
Park Lands and Mitcham (-34.967 to -34.940, 138.572 to 138.643); it is inside
AUADL's territory once that is widened, so it is a partition of Adelaide
rather than a city (the Melbourne councils' pattern).

Measured 2026-11-23 (layer data last edited 2025-08-01): 30,881 records --
27,790 `Tree`, 2,307 `Vacant Tree Location`, 759 `Removed`, 25 `Stump`.

Field choices:

  * **`STATUS = 'Tree'` only.**  Vacant locations, stumps and removed trees
    stay in the layer and are dropped with a logged count, as are the dozen
    `Tree` rows whose genus is itself `Stump` or `Vacant`.
  * **`tree_id` <- `ID`**, the asset system's record number (851253, ...),
    non-null and distinct over all 30,881 records.  `ASSET_ID` is the
    council's tag number but is blank on the 574 vacant locations and
    repeats on two pairs of neighbouring trees; `FID` is the export's row
    number (1..N) and renumbers on a re-export.  Prefixed `unl-`.
  * **`species`** is composed from `GENUS`, `SPECIES` and `VARIETY`.  The
    `SPECIES` column is usually the bare epithet, but on a few hundred rows
    it carries the whole binomial (`Corymbia ficifolia`, `Eucalyptus sp.`)
    or a cultivar (`Fraxinus` / `Raywood`); `compose_species` reads each
    shape, and `SOURCE_SPELLINGS` restores the handful that need a hand.
    A quoted `VARIETY` is the cultivar and is published as `cultivar` (its
    own column, so `'Glen's Form'`'s apostrophe survives); `'Grafted'` is a
    nursery note, not a cultivar, and is dropped.  `COMMON_NAM` is not
    mapped.  `Not specified`, `Unknown sp` and `Mixed Species` publish as
    `Unknown`.
  * **`diameter_at_breast_height` <- `DBH_VALUE`, centimetres** (median 30,
    and it sits inside the `DBH` band column, which is in cm: `0-8` ...
    `>75`).  3,154 zeros are unmeasured trees and are null; the schema
    nulls the odd slip (a 1,519 cm entry).
  * **`crown_width_m` <- `CANOPY`**, a crown-spread band in metres (`<6
    metres`, `6 -10 metres`, `11 - 15 metres`, `16 - 20 metres`, `>20
    metres`), published as the band midpoint (Ballarat's and Vienna's rule:
    wrong per tree by up to half a band, right in aggregate); the open top
    band takes 22.5 m.  Blank is unassessed.  An unknown band label counts
    as unreadable and more than 1% unreadable refuses the build.  `HEIGHT`
    is a height band and has no canonical column.
  * **`plant_date` <- `PLANTED_DA`**, a local date string (`2022-07-19
    00:00:00`) on ~2,600 trees planted from 2018 on (and two older); read as
    the date it names, no timezone conversion.  Blank on the rest.
  * Position is the layer geometry, requested in WGS84; it agrees with the
    `LAT`/`LNG` attributes to 1e-9 degrees.
"""

import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    is_not_a_tree,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)

# Kept in step with unley_update_time.py.
LAYER = FeatureLayer(
    "https://services9.arcgis.com/vsQXCXZ0LMZJANxk/arcgis/rest/services/"
    "Trees/FeatureServer/0",
    timeout=300,
)
OUT_FIELDS = "FID,ID,STATUS,GENUS,SPECIES,VARIETY,DBH_VALUE,CANOPY,PLANTED_DA"

TREE_STATUS = "Tree"

# Genus values that name no taxon: the tree is real, the name is not known.
UNNAMED_GENERA = frozenset({"not specified", "unknown sp", "unknown", "mixed species"})

# (GENUS, SPECIES) pairs that the generic composition would get wrong, to the
# name the inventory means.  `Raywood` is the claret ash, a selection of
# Fraxinus angustifolia (the VARIETY column spells it `'Raywoodii'`);
# `Harkness` is Callistemon 'Harkness', which Adelaide's register writes
# with its quote half lost; the Brachychiton rows carry the hybrid's other
# parent in VARIETY; `megalocarpa` is a subspecies written as a species.
SOURCE_SPELLINGS: dict[tuple[str, str, str], tuple[str, str | None]] = {
    ("Fraxinus", "Raywood", "'Raywoodii'"): ("Fraxinus angustifolia", "Raywood"),
    ("Callistemon", "Harkness", ""): ("Callistemon", "Harkness"),
    ("Eucalyptus", "Eucalyptus", "Torquata x woodwardii 'torwood'"): (
        "Eucalyptus torquata x woodwardii", "Torwood"
    ),
    ("Brachychiton", "acerifolius", "populneus"): ("Brachychiton acerifolius x populneus", None),
    ("Brachychiton", "populneus", "discolour"): ("Brachychiton populneus x discolor", None),
    ("Cotoneaster", "glaucophyllus", "serotinus"): ("Cotoneaster glaucophyllus var. serotinus", None),
    ("Eucalyptus", "leucoxylon", "rosea"): ("Eucalyptus leucoxylon", "Rosea"),
    # The red-flowered SA blue gum, E. leucoxylon subsp. megalocarpa 'Rosea';
    # read as written, `Eucalyptus megalocarpa` is no species, and the
    # nearest one (E. megacarpa, a Western Australian mallee) is wrong.
    ("Eucalyptus", "megalocarpa", "'Rosea'"): ("Eucalyptus leucoxylon subsp. megalocarpa", "Rosea"),
    ("Eucalyptus", "megalocarpa", ""): ("Eucalyptus leucoxylon subsp. megalocarpa", None),
}

# Unquoted VARIETY values that are cultivar names (the rest are quoted).
UNQUOTED_CULTIVARS = {"Todd": "Todd", "Merlot": "Merlot", "Lilac lady": "Lilac Lady"}

# Quoted VARIETY values that are nursery notes, not cultivars.
NOT_CULTIVARS = frozenset({"grafted"})

# Crown-spread band (whitespace removed, lower case) -> midpoint in metres.
CANOPY_BAND_M: dict[str, float] = {
    "<6metres": 3.0,
    "6-10metres": 8.0,
    "11-15metres": 13.0,
    "16-20metres": 18.0,
    ">20metres": 22.5,
}
MAX_UNPARSED_FRACTION = 0.01

_counts: Counter = Counter()


def clean(value) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def compose_species(genus: str, species: str, variety: str) -> tuple[str | None, str | None]:
    """(scientific name, cultivar) from the three name columns."""
    fixed = SOURCE_SPELLINGS.get((genus, species, variety))
    if fixed is not None:
        return fixed
    if not genus or genus.lower() in UNNAMED_GENERA:
        return None, None

    if " " in genus:
        # `Eucalyptus intertexta` / `intertexta`: the binomial is in GENUS.
        name = genus
    elif species.lower() in ("", "sp", "sp."):
        name = genus
    elif species.split()[0].lower() == genus.lower():
        # `Corymbia` / `Corymbia ficifolia`, `Eucalyptus` / `Eucalyptus sp.`
        name = species
    else:
        name = f"{genus} {species}"

    cultivar = None
    if variety:
        quoted = variety.strip("'‘’")
        if variety != quoted and quoted.lower() not in NOT_CULTIVARS:
            cultivar = quoted
        elif variety in UNQUOTED_CULTIVARS:
            cultivar = UNQUOTED_CULTIVARS[variety]
        elif quoted.lower() not in NOT_CULTIVARS:
            _counts["unmapped variety"] += 1
    # A cultivar the SPECIES column already quotes (`cerasifera 'Nigra'`) is
    # parsed out of the name by the schema; drop it from the name only when
    # the VARIETY column carries it, so the casing comes from one place.
    if cultivar and "'" in name:
        name = name[: name.index("'")].strip()
    return name, cultivar


def parse_canopy(value) -> float | None:
    label = "".join(clean(value).split()).lower()
    if not label:
        return None
    width = CANOPY_BAND_M.get(label)
    if width is None:
        _counts["unreadable canopy"] += 1
    return width


def parse_planted(value) -> date | None:
    text = clean(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        _counts["unreadable plant date"] += 1
        return None


def positive(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def keep(feature: dict) -> bool:
    """A standing, identified tree."""
    attrs = feature.get("attributes") or {}
    _counts["rows"] += 1
    status = clean(attrs.get("STATUS"))
    if status != TREE_STATUS:
        _counts[f"status {status or '(blank)'}"] += 1
        return False
    if is_not_a_tree(clean(attrs.get("GENUS"))):
        _counts["genus names a stump or vacancy"] += 1
        return False
    if attrs.get("ID") is None:
        _counts["no ID"] += 1
        return False
    return True


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        _counts["trees"] += 1
        name, cv = compose_species(
            clean(attrs.get("GENUS")), clean(attrs.get("SPECIES")), clean(attrs.get("VARIETY"))
        )
        lat, lon = esri_point(feature.get("geometry"))
        tree_id.append(f"unl-{int(attrs['ID'])}")
        species.append(normalize_species(name))
        cultivar.append(cv)
        plant_date.append(parse_planted(attrs.get("PLANTED_DA")))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(cm_to_inches(positive(attrs.get("DBH_VALUE"))))
        crown.append(parse_canopy(attrs.get("CANOPY")))

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["AUADL"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "cultivar": pa.array(cultivar, type=pa.string()),
        "plant_date": pa.array(plant_date, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        "crown_width_m": pa.array(crown, type=pa.float64()),
    })


def report_and_check() -> None:
    dropped = {k: v for k, v in _counts.items() if k.startswith(("status", "genus", "no ID"))}
    print(
        f"Unley: read {_counts['rows']} record(s), kept {_counts['trees']} trees; dropped "
        + ", ".join(f"{v} {k}" for k, v in sorted(dropped.items())),
        file=sys.stderr,
    )
    trees = _counts["trees"] or 1
    for what in ("unreadable canopy", "unreadable plant date", "unmapped variety"):
        count = _counts[what]
        if count:
            print(f"Unley: {count} {what} value(s)", file=sys.stderr)
        if count / trees > MAX_UNPARSED_FRACTION:
            raise RuntimeError(
                f"Unley: {count} of {trees} trees have an {what}; the source format has changed"
            )


if __name__ == "__main__":
    pages = iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, order_by="FID"
    )
    table = stream_to_table(pages, transform, keep=keep, label="Unley")
    report_and_check()
    table = validate_coordinates(table, city="Adelaide", city_code="AUADL")
    table = enforce_tree_schema(table, city="Adelaide", data_source="UNLEY")
    emit(table)

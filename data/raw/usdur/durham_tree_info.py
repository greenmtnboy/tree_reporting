#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Durham, NC's street-tree inventory.

Source: "Trees & Planting Sites", layer 11 of the city's
`PublicServices/Environmental` FeatureServer on webgis2.durhamnc.gov (the same
layer the Durham open data hub publishes as "Trees & Planting Sites"),
maintained by General Services' Urban Forestry division.  29,021 rows on
2026-11-11, of which 23,793 are trees and 23,792 are published.  The layer publishes no
`editingInfo`; paging, the watermark and Esri's epoch-milliseconds live in
`shared.platforms.arcgis`.

**A row is a site, and `present` says what is on it.**  Its coded domain is
`Tree` (23,689), `Dead` (104), `Planting Site` (4,897) and `Do Not Plant`
(286), plus 45 rows with no value -- 42 of them carry no genus, species, name
or diameter at all.  `Tree` and `Dead` are published: a dead tree is still a
standing tree, as in every other inventory here (they are mostly 2025-26
plantings that failed their first summer).  `Planting Site`, `Do Not Plant`
and a null are dropped; any other value raises, because a new status is a
schema change and guessing which side of the line it falls on would publish
the wrong trees.  One `Tree` row sits at (0, 0) -- a point with no real
geometry -- and is dropped with a logged count.

**The species is two columns**, `genus` and `species` (the epithet), both
coded-value domains whose codes are the names themselves.  The epithet domain
carries the hybrid mark (`x grandiflora`, `x soulangiana`) and a couple of
quoted cultivars, which the shared hygiene moves to `cultivar`, and two
unquoted cultivars posing as epithets (`okame`, `x Regal Prince`), which this
ingest moves itself (`EPITHET_CULTIVARS`).  On 741 trees
`genus` is blank but `commonname` is filled (`Serviceberry`, `Blackgum`,
`Okame Cherry`, ...); for those, and only those, the species comes from the
curated `shared.species.english` table, which resolves 611 of them.  Where a
genus is recorded the publisher's binomial wins, even when the common name is
more specific (3,387 `Lagerstroemia` rows read `Common Crapemyrtle`).  Typos
in the domains (`Carpinus carolina`, `Juniperus virginicus`) are folded
centrally by `SPECIES_MISSPELLINGS`, not here.

`commonname` is a coded domain whose codes are the names; it is the tree
card's `tree_name`.  `variety` is the cultivar column (one row uses it).
`diameterin` is DBH in inches.  `heightft` is a height band, not a crown
spread, so no `crown_width_m` is published.

**`plant_date` is the planting season.**  `plantingdate` is a coded domain of
fiscal-year seasons (`2016-2017` ... `2029-2030`), the city's Nov-Mar planting
window; a season is dated 1 January of its second year, the middle of that
window.  The one tree marked for the current `2026-2027` season dates in the
future and is nulled by the shared schema.  There is no stamped default date.
`pitinstalldate` dates a tree-pit treatment, `contractplantingyr` is the
contract's year index (1-3), and `created_date` is the GIS edit, so none of
them is a planting date.

`GlobalID` is the key: 29,021 distinct, no nulls.  `facilityid` (aliased
"Tree: Id") is null on 2,245 rows and repeats on 20; `assetid` is also unique
but the publisher guarantees only the GlobalID.
"""

import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    emit,
    enforce_tree_schema,
    normalize_species_parts,
    normalize_tree_name,
    stream_to_table,
    validate_coordinates,
)
from shared.species.english import species_from_common_name

LAYER = FeatureLayer(
    "https://webgis2.durhamnc.gov/server/rest/services/"
    "PublicServices/Environmental/FeatureServer/11"
)

OUT_FIELDS = "GlobalID,present,genus,species,commonname,variety,plantingdate,diameterin"

TREE_PRESENT = frozenset({"Tree", "Dead"})
NOT_A_TREE_PRESENT = frozenset({"Planting Site", "Do Not Plant", ""})

# Epithet-domain codes that are cultivars, not species: `Prunus okame` is the
# cherry cultivar 'Okame' (P. incisa x campanulata), 92 trees; `x Regal Prince`
# is Quercus x warei 'Regal Prince' (no tree uses it yet).  The genus is kept
# and the name moves to `cultivar`.
EPITHET_CULTIVARS = {"okame": "Okame", "x Regal Prince": "Regal Prince"}

SEASON = re.compile(r"(\d{4})-(\d{4})")


def is_a_tree(present: str | None) -> bool:
    """True for a standing (live or dead) tree; raises on an unseen value."""
    value = (present or "").strip()
    if value in TREE_PRESENT:
        return True
    if value in NOT_A_TREE_PRESENT:
        return False
    raise RuntimeError(
        f"Durham `present` value {value!r} is new; decide whether it is a tree"
    )


def clean_text(value: str | None) -> str | None:
    """Collapse whitespace; blank and `N/A` are missing."""
    text = " ".join((value or "").split())
    return text if text and text.upper() != "N/A" else None


def plant_date_of(season: str | None) -> date | None:
    """1 January of a `YYYY-YYYY` planting season's second year."""
    value = clean_text(season)
    if value is None:
        return None
    match = SEASON.fullmatch(value)
    if not match or int(match[2]) != int(match[1]) + 1:
        raise RuntimeError(f"Durham planting season {value!r} is not YYYY-YYYY")
    return date(int(match[2]), 1, 1)


def species_of(rec: dict) -> str | None:
    """The recorded genus and epithet; the common name only when no genus is."""
    genus = clean_text(rec.get("genus"))
    if genus:
        epithet = clean_text(rec.get("species"))
        if epithet in EPITHET_CULTIVARS:
            return genus
        return normalize_species_parts(genus, epithet)
    return species_from_common_name(clean_text(rec.get("commonname")))


def cultivar_of(rec: dict) -> str | None:
    """`variety`, else a cultivar the epithet domain carries as an epithet."""
    if variety := clean_text(rec.get("variety")):
        return variety
    if clean_text(rec.get("genus")):
        return EPITHET_CULTIVARS.get(clean_text(rec.get("species")) or "")
    return None


def is_null_island(lat: float | None, lon: float | None) -> bool:
    return lat is not None and lon is not None and abs(lat) < 1e-6 and abs(lon) < 1e-6


def iter_row_chunks():
    return iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    )


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    tree_name: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    null_island = 0

    for feature in features:
        rec = feature.get("attributes") or {}
        if not is_a_tree(rec.get("present")):
            continue
        lat, lon = esri_point(feature.get("geometry"))
        if is_null_island(lat, lon):
            null_island += 1
            continue
        raw_id = str(rec.get("GlobalID") or "").strip()
        tree_id.append(f"dur-{raw_id}" if raw_id else None)
        species.append(species_of(rec))
        cultivar.append(cultivar_of(rec))
        tree_name.append(normalize_tree_name(clean_text(rec.get("commonname"))))
        plant_date.append(plant_date_of(rec.get("plantingdate")))
        latitude.append(lat)
        longitude.append(lon)
        raw_dbh = rec.get("diameterin")
        dbh.append(float(raw_dbh) if raw_dbh else None)

    if null_island:
        print(f"Durham: dropped {null_island} tree(s) at (0, 0)", file=sys.stderr)

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["USDUR"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "tree_name": pa.array(tree_name, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Durham OpenData")
    table = validate_coordinates(table, city="Durham, NC", city_code="USDUR")
    table = enforce_tree_schema(
        table, city="Durham, NC", data_source="DURHAM_OPENDATA"
    )
    emit(table)

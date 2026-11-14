#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Atlanta's trees, from Trees Atlanta's plant inventory.

Source: "Trees Atlanta Plant Inventory" (`Trees_Atlanta_Plant_Inventory_Public_View/
FeatureServer/0`, item 9a8404b7b48d499ab76b36c237434542) on Trees Atlanta's
own ArcGIS Online organisation (`Data_TA`), the public view behind the tree map
on the nonprofit's website: "all public-facing data about plants that Trees
Atlanta has planted since 1985".  It is the nonprofit's planting record, not
the City of Atlanta's street-tree inventory, so it covers what Trees Atlanta
planted and maintains, across the metro (its own `City` column names thirty
municipalities; 101,827 rows read Atlanta).  The item states no licence or
terms of use (`licenseInfo` and `accessInformation` are empty); the layer is
public and anonymous.  128,154 rows on 2026-11-11.  Paging, the watermark and
Esri's epoch-milliseconds live in `shared.platforms.arcgis`.

**Which rows are trees.**  Two columns decide it, and each raises on a value
the ingest has not seen, because guessing which side of the line a new code
falls on would publish the wrong plants:

* `Status` is the plant's condition at its last check.  `Alive` (110,034)
  stays; `Dead` (15,243), `VandalizedDead` and its spelled-out twin
  `Vandalized Dead` (1,039 + 1,763) and `NotFound` (11, the crew could not
  find the plant) are dropped.  A null or blank status (54 + 8) is a planting
  never re-inspected and `ReadyToUpload` (2) a record still in the crews'
  workflow; both are planted trees and stay.
* `ForestLayer` is the plant's habit.  `Shrub` (10,857), `Groundcover` (378)
  and `Vine` (218) are dropped -- the inventory records every plant in a
  forest-restoration or green-infrastructure site, down to grasses.
  `Overstory`, `Understory` and the older crews' codes `CommunityTree`,
  `NewTree`, `UrbanTree` and `NewTreeOverstory` stay, and so does a null layer
  (9,512 rows, overwhelmingly oaks, magnolias, fringetrees and pines).
* A `PlantingSeason` of `STUMP` (2 rows, no genus) marks a stump, not a tree.

That leaves 99,376 trees on 2026-11-11.  `validate_coordinates` then drops
9 with no geometry and 3,464 outside USATL's sanity box (Sandy Springs,
Dunwoody, South Fulton, Clarkston, unincorporated DeKalb and further out),
publishing 95,903.

**The species is two columns**, `Genus` and `Species` (the epithet), both
coded-value domains read here at run time.  Most codes are the names
themselves; the rest are run together (`canadensisvaralba` is `canadensis
var. alba`, `xGordlinia` is `x Gordlinia`), so every value is decoded through
its domain before it reaches `normalize_species_parts`, and a value outside the
domain (`copallina`, `tulipfera`) is used as written, except a run-together
infraspecific code outside it (`falcatavarpagodifolia`, 7 rows), which keeps
its species epithet.  A hybrid genus (`x Gordlinia`) keeps its mark in front
of the name, and its epithet's own mark (`xgrandiflora`) is dropped.  A blank genus, the bare
hybrid mark `x`, `Unknown` and `MixedPlanting` / `MixedPlaning` carry no taxon
and leave `species` empty, which the schema turns into `Unknown` (611
published rows).  Spelling errors (`tulipfera`, `virginiaina`, the genus
`Chimonanthus` written for `Chionanthus virginicus`) are folded centrally by
`SPECIES_MISSPELLINGS`, not here.  About fifty rows with a null
`ForestLayer` are herbaceous (`Carex`, `Andropogon`, `Yucca`); they are too
few to justify a genus list and stay.

`Cultivar` has no domain and stores the name with its spaces taken out
(`AutumnBrilliance`, `NellieRStevens`, `WhitXXV`); the words are split back
apart at case changes.  `StraightSpecies` and `NewCultivar` are not cultivars
and become null.  There is no common-name column, so `tree_name` is null and
the map falls back to the enrichment table's name.

**There is no diameter.**  `PlantedSize` is the nursery stock (`15gal`,
`2.5cal`) at planting: a container volume or a caliper measured six inches
above the root flare, neither of which is a diameter at breast height, so
`diameter_at_breast_height` is null.

**`plant_date` is `PlantedDate`**, the day the crew recorded the planting
(119,173 of 128,154 rows, 1990-2026).  About 4,200 read 1 January of a
year, which is how the older records stored a year without a day; those are
kept, since the year is right.  `PlantingSeason` (`21-22`) is *not* turned
into a date for the rows without one: a season runs from autumn into spring
and its dated plantings split about 40/60 between its two calendar years, so
neither year is the right answer for an undated tree and the date stays
null.  Where a dated planting disagrees with its season (2,021 rows), the
date wins.

`GlobalID` is the key: 128,154 distinct, no nulls.  `OBJECTID` is the view's
own row number and is not stable across a republish of the view.
"""

import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    coded_value_domain,
    esri_ms_to_date,
    esri_point,
    iter_features,
)
from shared.ingest import (
    emit,
    enforce_tree_schema,
    normalize_species_parts,
    normalize_tree_name,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services6.arcgis.com/BZn8g8tzu5WfMokL/arcgis/rest/services/"
    "Trees_Atlanta_Plant_Inventory_Public_View/FeatureServer/0"
)

OUT_FIELDS = "GlobalID,Genus,Species,Cultivar,Status,ForestLayer,PlantingSeason,PlantedDate"

KEPT_STATUSES = frozenset({"Alive", "", "ReadyToUpload"})
DROPPED_STATUSES = frozenset({"Dead", "VandalizedDead", "Vandalized Dead", "NotFound"})

TREE_LAYERS = frozenset(
    {"Overstory", "Understory", "CommunityTree", "NewTree", "UrbanTree", "NewTreeOverstory", ""}
)
NOT_TREE_LAYERS = frozenset({"Shrub", "Groundcover", "Vine"})

STUMP_SEASON = "STUMP"

# Genus values that name no taxon: the bare hybrid mark and a mixed bed.
NO_TAXON_GENERA = frozenset({"x", "unknown", "mixedplanting", "mixed planting", "mixedplaning"})

# Cultivar values that say there is no cultivar.
NOT_CULTIVARS = frozenset({"straightspecies", "newcultivar"})

# A word boundary inside a run-together cultivar: lower to upper
# (`Autumn|Brilliance`, but not `Mc|K`) and upper to capitalised word
# (`R|Stevens`, `K|Sentry`).
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?<!Mc)(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

# `falcatavarpagodifolia`: an epithet, a rank and an infraspecific epithet run together.
_RUN_TOGETHER_RANK = re.compile(r"^([a-z-]+?)(?:var|subsp|ssp)([a-z-]+)$")

# Left-to-right marks and zero-width characters pasted into a few genus values.
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍‎‏﻿"))


def clean_text(value: str | None) -> str | None:
    """Collapse whitespace (non-breaking included) and drop invisible marks."""
    text = " ".join(str(value or "").translate(_INVISIBLE).split())
    return text or None


def is_a_tree(rec: dict) -> bool:
    """True for a living tree; raises on a status or habit the ingest has not seen."""
    status = (rec.get("Status") or "").strip()
    if status in DROPPED_STATUSES:
        return False
    if status not in KEPT_STATUSES:
        raise RuntimeError(f"Atlanta Status {status!r} is new; decide whether it is a living tree")
    layer = (rec.get("ForestLayer") or "").strip()
    if layer in NOT_TREE_LAYERS:
        return False
    if layer not in TREE_LAYERS:
        raise RuntimeError(f"Atlanta ForestLayer {layer!r} is new; decide whether it is a tree")
    return (rec.get("PlantingSeason") or "").strip() != STUMP_SEASON


def decode_epithet(code, epithets: dict) -> str | None:
    """An epithet code through its domain.

    A code outside the domain is used as written, except a run-together
    infraspecific name (`falcatavarpagodifolia`), which keeps its species
    epithet when that epithet is itself in the domain; the rank below species
    is dropped centrally anyway.
    """
    if code in epithets:
        return clean_text(epithets[code])
    text = clean_text(code)
    if text:
        match = _RUN_TOGETHER_RANK.match(text)
        if match and match.group(1) in epithets.values():
            return match.group(1)
    return text


def species_of(genus_code, epithet_code, genera: dict, epithets: dict) -> str | None:
    """The binomial from the two coded columns, decoded through their domains."""
    genus = clean_text(genera.get(genus_code, genus_code))
    epithet = decode_epithet(epithet_code, epithets)
    if not genus or genus.lower() in NO_TAXON_GENERA:
        return None
    hybrid = ""
    if genus.lower().startswith("x ") and len(genus) > 2:
        hybrid, genus = "x ", genus[2:].strip()
        # One hybrid mark per name: `x Gordlinia` with the epithet `x grandiflora`.
        if epithet and epithet.lower().startswith("x "):
            epithet = epithet[2:].strip()
    name = normalize_species_parts(genus, epithet)
    return f"{hybrid}{name}" if name else None


def cultivar_of(value) -> str | None:
    text = clean_text(value)
    if not text or text.replace(" ", "").lower() in NOT_CULTIVARS:
        return None
    return " ".join(_CAMEL_BOUNDARY.sub(" ", text).split())


def iter_row_chunks():
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


def make_transform():
    genera = coded_value_domain(LAYER, "Genus")
    epithets = coded_value_domain(LAYER, "Species")
    dropped = {"rows": 0}

    def transform(features: list[dict]) -> pa.Table:
        tree_id: list[str | None] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        tree_name: list[str | None] = []
        plant_date: list[date | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            if not is_a_tree(rec):
                dropped["rows"] += 1
                continue
            raw_id = str(rec.get("GlobalID") or "").strip().strip("{}")
            tree_id.append(f"atl-{raw_id}" if raw_id else None)
            species.append(species_of(rec.get("Genus"), rec.get("Species"), genera, epithets))
            cultivar.append(cultivar_of(rec.get("Cultivar")))
            tree_name.append(normalize_tree_name(None))
            plant_date.append(esri_ms_to_date(rec.get("PlantedDate")))
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            dbh.append(None)

        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["USATL"] * len(tree_id), type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "tree_name": pa.array(tree_name, type=pa.string()),
                "plant_date": pa.array(plant_date, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            }
        )

    return transform, dropped


if __name__ == "__main__":
    transform, dropped = make_transform()
    table = stream_to_table(iter_row_chunks(), transform, label="Trees Atlanta")
    print(
        f"Trees Atlanta: dropped {dropped['rows']} dead, missing, shrub, groundcover, "
        f"vine and stump rows; {table.num_rows} trees",
        file=sys.stderr,
    )
    table = validate_coordinates(table, city="Atlanta", city_code="USATL")
    table = enforce_tree_schema(table, city="Atlanta", data_source="TREES_ATLANTA")
    emit(table)

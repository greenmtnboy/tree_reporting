#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Calgary's municipal tree inventory, from the city's Socrata portal.

Source: "Public Trees" (`tfs4-3wwa`) on data.calgary.ca, 581,011 rows -- the
largest Canadian inventory on the map and the second largest anywhere in it
after London.  Paging, the freshness watermark and the point-column shapes
live in `_socrata_shared`.

**The id is `wam_id`, not `tree_asset_cd`.**  The obvious-looking column is a
trap of exactly the kind `EXTENDING.md` describes under "`tree_id` is the
grain": `tree_asset_cd` has **5,908 distinct values across 581,011 rows** and
192 nulls, because the portal documents it as "the short-hand code used to
identify the community and park number an asset resides in" -- a location code
shared by every tree in a park.  Keyed on it, `tree_id` would have repeated a
hundredfold and fanned out every join built on the grain.  `wam_id` is
documented as "the unique identifier used to identify Parks assets.  Each tree
has its own WAM_ID", and measures 581,011 distinct with zero nulls.

Calgary writes everything in capitals and inverts its common names for
sorting (`ASH, GREEN`), so the presentation columns need putting back the right
way up -- `normalize_tree_name` for the common name, `normalize_cultivar` for
the cultivar the portal publishes in its own column.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    normalize_species_parts,
    normalize_tree_name,
    stream_to_table,
    validate_coordinates,
)
from _socrata_shared import SocrataDataset, iter_rows, point_lon_lat

DATASET = SocrataDataset("data.calgary.ca", "tfs4-3wwa", timeout=180)

SELECT = (
    "wam_id,asset_type,genus,species,cultivar,common_name,dbh_cm,point"
)

# `asset_type` splits the layer three ways: TREE (500,035), SHRUB (39,268) and
# STUMP (41,708).  A stump is a site with no living plant in it, which is the
# case `is_not_a_tree` already drops for the portals that say so in the species
# field -- Calgary says it in a column instead, so the filter lives here.
#
# Shrubs are kept.  They are living plants at a real location that the city
# identified to species, and this repo already renders a shrub: `SHRUB_SPECIES`
# is one of the four form sentinels precisely so a source that records the
# growth form and nothing else still gets a dot and an icon.  Dropping 39,268
# identified plants would be a stricter rule than the one applied to the
# sources that record less.
NOT_A_TREE_ASSET_TYPES = frozenset({"STUMP"})

# Calgary's dbh_cm tops out at 250, which is plausible for a prairie elm.  A
# recorded 0 is an unmeasured tree rather than a tree of no width.
MIN_DBH_CM = 1.0


def normalize_cultivar(value: str | None) -> str | None:
    """"SPRING SNOW" -> "Spring Snow".

    The whole portal is upper case, so unlike `extract_cultivar` -- which
    leaves casing alone because a nursery code is case-significant -- there is
    no information in Calgary's capitals to preserve.
    """
    if not value:
        return None
    text = " ".join(value.split())
    if not text:
        return None
    return " ".join(word[:1].upper() + word[1:].lower() for word in text.split(" "))


def parse_dbh(value) -> float | None:
    """Centimetres to inches.  A recorded 0 is unmeasured, not zero-width."""
    try:
        cm = float(value)
    except (TypeError, ValueError):
        return None
    if cm < MIN_DBH_CM:
        return None
    return cm_to_inches(cm)


def is_a_tree(record: dict) -> bool:
    """False for Calgary's stump records.  See `NOT_A_TREE_ASSET_TYPES`.

    Passed to `stream_to_table` as its `keep` filter rather than applied inside
    `transform`, so the dropped rows never occupy a column and the run reports
    how many it dropped.
    """
    asset_type = (record.get("asset_type") or "").strip().upper()
    return asset_type not in NOT_A_TREE_ASSET_TYPES


def iter_row_chunks():
    """One Socrata page at a time.

    A generator, not a bulk read: 581k records held simultaneously as a
    response body and a dict each is the whole-city peak that OOM-killed
    Washington DC's 2 GiB container at 216k.
    """
    return iter_rows(DATASET, select=SELECT)


def transform(rows: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    tree_name: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for rec in rows:
        raw_id = (rec.get("wam_id") or "").strip()
        if not raw_id:
            continue
        lon, lat = point_lon_lat(rec.get("point"))

        tree_id.append(f"cal-{raw_id}")
        species.append(normalize_species_parts(rec.get("genus"), rec.get("species")))
        cultivar.append(normalize_cultivar(rec.get("cultivar")))
        tree_name.append(normalize_tree_name(rec.get("common_name")))
        # The layer carries ACTIVE_DT, which the portal does not describe.  Its
        # values run 1893-2026 and rise smoothly with no migration spike, so it
        # is *probably* a planting date -- but "probably" is not good enough to
        # put a year on 454,485 tree cards, and Denver set the precedent of
        # publishing no plant_date rather than passing off a date that means
        # something else.  One reply from the city's open data team flips this
        # to `parse_plant_date(rec.get("active_dt"))` and nothing else.
        plant_date.append(None)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(parse_dbh(rec.get("dbh_cm")))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CACAL"] * len(tree_id), type=pa.string()),
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
    table = stream_to_table(
        iter_row_chunks(), transform, keep=is_a_tree, label="Calgary OpenData"
    )
    table = validate_coordinates(table, city="Calgary", city_code="CACAL")
    table = enforce_tree_schema(
        table, city="Calgary", data_source="CALGARY_OPENDATA"
    )
    emit(table)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy", "tzdata"]
# ///

"""Ballarat's exceptional trees: the City of Ballarat's Exceptional Tree Register.

Source: "Exceptional Tree Register" (`exceptional-tree-register`) on the
city's OpenDataSoft portal, data.ballarat.vic.gov.au, CC BY 3.0 AU like the
inventory.  150 entries on 2026-11-16 (portal `modified` 2023-12-04): 90 on
council land, 53 in private gardens, 36 also on the National Trust's register.
Each records the twelve criteria it was assessed against (`Yes`/`No`), a
banded trunk and height, a written statement and, for 18 entries, a group
(`number_of`: a pair of cedars, 36 walnuts, the 3,801 trees of the Avenue of
Honour).

The `NOTABLE_AUBAL` partition (tree_dedup.preql, "notable row"):
  * **Linked by asset id, not by the spatial rule.**  The register is a view
    of the same asset system as the inventory: every `central_asset_id` but
    one is an inventory `ref`, at the same point (1.4-1.6 m apart, the two
    layers' projections; three group entries 11-72 m, the group's other
    end) and with the register's trunk band copied into the inventory's DBH.  So each row links to `bal-{central_asset_id}`, read
    from ballarat_tree_info.py's own table so the id exists in the build.  The
    one entry the inventory does not hold (an eastern cottonwood in the
    Botanical Gardens, 00157318) is dropped by `notable_table`.
  * **No name.**  `feature_location` is a species and an address ("Camperdown
    Elm 315 Eureka Street"), a description and not a tree's own name.
  * **The significance** says what the register says: which criteria the tree
    was listed for, whether it stands for a group, and whether the National
    Trust lists it too.  The city's written statement is not copied: it is
    long, and repeats the trunk and height the card already shows.
  * **No trunk.**  The register's DBH is a band (`> 90cm`), not a measure, and
    the inventory row already carries it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (  # noqa: E402
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    validate_coordinates,
)
from shared.notable import notable_table  # noqa: E402

DATASET = "exceptional-tree-register"
EXPORT_URL = (
    "https://data.ballarat.vic.gov.au/api/explore/v2.1/catalog/datasets/"
    f"{DATASET}/exports/json"
)
LANDING_URL = f"https://data.ballarat.vic.gov.au/explore/dataset/{DATASET}/"
DATA_SOURCE = "NOTABLE_AUBAL"

# The register's criteria, in its column order, as the reason they give.
CRITERIA = {
    "outstanding_size": "its size",
    "particularly_old": "its age",
    "outstanding_example": "being an outstanding example of its species",
    "rare_or_localised": "its rarity",
    "curious_growth_form": "its unusual form",
    "aesthetic_value": "its beauty",
    "horticultural_value": "its horticultural value",
    "location_context": "its place in the landscape",
    "outstanding_habitat_value": "its value as habitat",
    "historical_value": "its history",
    "aboriginal_association": "its Aboriginal associations",
    "social_cultural_spiritual": "its social, cultural or spiritual value",
}

# Group entries of more than one taxon.  Not names: `sanitize_species` would
# keep `Native` as a genus, and the notable row would carry it into the merge.
COLLECTIVE_SPECIES = frozenset({"native sp.", "mixed sp."})

REQUIRED = ("central_asset_id", "species", "number", "national_trust", "geo_point_2d", *CRITERIA)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def is_yes(value) -> bool:
    return (clean(value) or "").lower() == "yes"


def species_of(value) -> str | None:
    text = clean(value)
    return None if text is None or text.lower() in COLLECTIVE_SPECIES else text


def significance_for(record: dict) -> str:
    reasons = [reason for column, reason in CRITERIA.items() if is_yes(record.get(column))]
    text = "On the City of Ballarat's Exceptional Tree Register"
    if reasons:
        listed = reasons[0] if len(reasons) == 1 else ", ".join(reasons[:-1]) + " and " + reasons[-1]
        text += f" for {listed}"
    text += "."
    count = record.get("number")
    if isinstance(count, int) and count > 1:
        text += f" Listed as a group of {count:,} trees."
    if is_yes(record.get("national_trust")):
        text += " Also on the National Trust's Register of Significant Trees."
    return text


def fetch_records() -> list[dict]:
    records = get_json_with_retry(EXPORT_URL, timeout=120)
    if not isinstance(records, list):
        raise RuntimeError(f"Ballarat exceptional trees: expected a list, got {type(records).__name__}")
    if records:
        missing = [c for c in REQUIRED if c not in records[0]]
        if missing:
            raise RuntimeError(f"Ballarat exceptional trees: columns {missing} are missing")
    return records


def register_rows(records: list[dict]) -> list[dict]:
    rows = []
    for record in records:
        asset = clean(record.get("central_asset_id"))
        point = record.get("geo_point_2d") or {}
        if not asset:
            continue
        rows.append(
            {
                "tree_id": f"bal-etr-{asset}",
                "asset": asset,
                "species": species_of(record.get("species")),
                "tree_name": None,
                "latitude": point.get("lat"),
                "longitude": point.get("lon"),
                "dbh_in": None,
                "significance": significance_for(record),
                "significance_source": LANDING_URL,
            }
        )
    return rows


def links_for(rows: list[dict], inventory: pa.Table) -> list[str | None]:
    """Each row's inventory tree: the asset the register names, when held."""
    held = set(inventory.column("tree_id").to_pylist())
    links = [f"bal-{row['asset']}" if f"bal-{row['asset']}" in held else None for row in rows]
    print(
        f"Ballarat exceptional trees: {sum(1 for l in links if l)} of {len(rows)} "
        "name an asset the inventory holds",
        file=sys.stderr,
    )
    return links


def build(records: list[dict], inventory: pa.Table) -> pa.Table:
    rows = register_rows(records)
    table = notable_table(rows, links_for(rows, inventory), city="AUBAL")
    table = validate_coordinates(table, city="Ballarat", city_code="AUBAL")
    return enforce_tree_schema(table, city="Ballarat", data_source=DATA_SOURCE, proper_names=True)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    from ballarat_tree_info import load_published  # noqa: E402

    emit(build(fetch_records(), load_published()))

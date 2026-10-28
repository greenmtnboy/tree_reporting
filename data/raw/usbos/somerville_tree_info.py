#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Somerville MA public trees, from the city's TreeKeeper inventory.

Somerville publishes no tree dataset on its open-data portal; the inventory
lives in Davey's TreeKeeper (https://somervillema.treekeepersoftware.com),
which the Urban Forestry page links as the public map.  Davey re-inventoried
the city in 2024 and staff keep it current, so it is the live record.  How
the public map is read is `shared.platforms.treekeeper`.

Somerville borders Cambridge for most of its southern edge, and both cities
inventory the trees on the streets they share.  That overlap is resolved in
`boston_tree_info.preql`, which sees every metro source; this ingest knows
only its own portal.
"""

import sys
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (  # noqa: E402
    emit,
    enforce_tree_schema,
    is_not_a_tree,
    normalize_species,
    validate_coordinates,
)
from shared.platforms.treekeeper import (  # noqa: E402
    TreeKeeperSite,
    fetch_sites,
    newest_value,
    split_label,
    tree_name_for,
)

# The "Smart Tree Inventory" layer; the box is generously around the city
# (42.373-42.418N, 71.134-71.075W).
SITE = TreeKeeperSite(
    "https://somervillema.treekeepersoftware.com/",
    map_layer="main_layer14",
    grid_layer=14,
    bbox=(42.33, 42.46, -71.20, -71.00),
)

# Grid columns (`SITE_ATTR*` are per-project).
SPECIES = "SITE_ATTR41"  # "maple: Norway (Acer platanoides)"
DBH_INCHES = "SITE_ATTR25"
INVENTORY_DATE = "SITE_INVENTORYDATE"  # "7/21/2024"


def parse_inventory_date(raw: str | None) -> date | None:
    if not raw:
        return None
    return datetime.strptime(raw, "%m/%d/%Y").date()


def newest_inventory_date() -> date | None:
    """The latest `Inventory Date` on any site."""
    return parse_inventory_date(newest_value(SITE, INVENTORY_DATE))


def build_table(rows: list[dict]) -> tuple[pa.Table, dict[str, int]]:
    dropped = {"no species": 0, "not a tree": 0}
    tree_ids: list[str] = []
    species_list: list[str] = []
    tree_names: list[str | None] = []
    latitudes: list[float | None] = []
    longitudes: list[float | None] = []
    dbhs: list[float | None] = []

    for row in rows:
        common, scientific = split_label(row.get(SPECIES))
        # Stumps and vacant sites are rows here: TreeKeeper inventories the
        # planting site, and the site outlives its tree.
        if is_not_a_tree(scientific) or is_not_a_tree(common):
            dropped["not a tree"] += 1
            continue
        species = normalize_species(scientific)
        if species is None:
            dropped["no species"] += 1
            continue
        tree_ids.append(f"som-{row['SITE_ID']}")
        species_list.append(species)
        tree_names.append(tree_name_for(common))
        latitudes.append(row.get("LATITUDE"))
        longitudes.append(row.get("LONGITUDE"))
        dbh = row.get(DBH_INCHES)
        dbhs.append(float(dbh) if dbh is not None else None)

    table = pa.table(
        {
            "tree_id": pa.array(tree_ids, type=pa.string()),
            "city": pa.array(["USBOS"] * len(tree_ids), type=pa.string()),
            "species": pa.array(species_list, type=pa.string()),
            "tree_name": pa.array(tree_names, type=pa.string()),
            "latitude": pa.array(latitudes, type=pa.float64()),
            "longitude": pa.array(longitudes, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbhs, type=pa.float64()),
        }
    )
    return table, dropped


if __name__ == "__main__":
    table, dropped = build_table(fetch_sites(SITE))
    for reason, count in dropped.items():
        if count:
            print(f"Somerville ingest: dropped {count} sites with {reason}", file=sys.stderr)
    table = validate_coordinates(table, city="Somerville", city_code="USBOS")
    table = enforce_tree_schema(table, city="Somerville", data_source="SOMERVILLE")
    emit(table)

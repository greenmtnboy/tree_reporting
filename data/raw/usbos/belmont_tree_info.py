#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Belmont MA public trees, from the town's TreeKeeper inventory.

Belmont publishes no tree dataset on an open-data portal; the inventory lives
in Davey's TreeKeeper (https://belmontma.treekeepersoftware.com), which the
town's website links as its public tree map.  The 2022 inventory recorded
some 8,000 trees along streets, in parks, around public buildings and in the
cemeteries, and staff keep it current.  How the public map is read is
`shared.platforms.treekeeper`.

Belmont's public grid exposes no date column (no inventory date, no last
edit), so there is no freshness probe: Belmont's edits publish when another
Boston source moves, which the City of Boston and Cambridge feeds do monthly.

Of Belmont's neighbours only Cambridge is another inventory in this model;
trees both towns record on that edge are resolved in `boston_tree_info.preql`,
which sees every metro source.
"""

import sys
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
    split_label,
    tree_name_for,
)

# The "Tree Sites" layer; the box is generously around the town
# (42.375-42.415N, 71.205-71.154W).  The "Memorial Tree" layer is a styled
# view of the same sites, not more of them.
SITE = TreeKeeperSite(
    "https://belmontma.treekeepersoftware.com/",
    map_layer="main_layer",
    grid_layer=1,
    bbox=(42.33, 42.46, -71.30, -71.10),
)

# Grid columns (`SITE_ATTR*` are per-project).
SPECIES = "SITE_ATTR1"  # "Maple, Norway (Acer platanoides)"
DBH_INCHES = "SITE_ATTR7"  # whole inches; 0 on every vacant site and some trees


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
        tree_ids.append(f"bel-{row['SITE_ID']}")
        species_list.append(species)
        tree_names.append(tree_name_for(common))
        latitudes.append(row.get("LATITUDE"))
        longitudes.append(row.get("LONGITUDE"))
        # A zero is the unmeasured default, not a sapling under half an inch:
        # it is what every vacant site carries.
        dbh = row.get(DBH_INCHES)
        dbhs.append(float(dbh) if dbh else None)

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
            print(f"Belmont ingest: dropped {count} sites with {reason}", file=sys.stderr)
    table = validate_coordinates(table, city="Belmont", city_code="USBOS")
    table = enforce_tree_schema(table, city="Belmont", data_source="BELMONT")
    emit(table)

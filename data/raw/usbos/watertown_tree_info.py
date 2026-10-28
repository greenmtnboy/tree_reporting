#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Watertown MA public trees, from the city's 2022 inventory on MapGeo.

Watertown publishes no tree dataset on an open-data portal.  PlanIT Geo
inventoried the public trees in 2022 (streets, parks, public property) under a
state DCR grant, and the city shows the result as the "2022 Tree Inventory"
theme of its MapGeo site (https://watertownma.mapgeo.io).  That theme is a
public CARTO named map; how it is read is `shared.platforms.carto`.  The
city's older OpenTreeMap (opentreemap.org/watertownma) no longer serves data.

The inventory is a snapshot: every site was added and last modified in 2022.
So there is no freshness probe; were the city to edit it, the edit would
publish with the next change to any other Boston source.

Watertown borders Cambridge, Belmont and Boston (Brighton), all inventories in
this model; trees two of them record are resolved in `boston_tree_info.preql`.
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
    normalize_tree_name,
    validate_coordinates,
)
from shared.platforms.carto import NamedMap, fetch_points  # noqa: E402

# The "2022 Tree Inventory" theme's layer, `watertownma_2022treeinventory`.
INVENTORY = NamedMap(
    "https://mapstudio.mapgeo.io/user/mapgeoprod",
    "tpl_4341bef6_c15b_11ed_bde7_8ec690ec29cb",
)
# Generously around the city (42.360-42.385N, 71.214-71.144W).
BBOX = (42.33, 42.40, -71.22, -71.12)

# `status` is Alive, Dead, Stump or "Proposed Site - {size}"; a dead tree is
# still a standing tree, as in every other inventory here.
TREE_STATUSES = {"Alive", "Dead"}
# The portal's "nothing here" for a text column.
BLANKS = {"", "NULL"}
# Values the shared species cleanup cannot read: 27 genus-level rows whose
# `scientific` is the common name (code ULS, Ulmus species), and one female
# ginkgo whose sex rides in the binomial after a comma.
SCIENTIFIC_FIXES = {"Elm": "Ulmus", "Gingko biloba, female": "Ginkgo biloba"}


def text(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return None if value in BLANKS else value


def build_table(rows: list[dict]) -> tuple[pa.Table, dict[str, int]]:
    dropped = {"no species": 0, "not a tree": 0}
    tree_ids: list[str] = []
    species_list: list[str] = []
    tree_names: list[str | None] = []
    latitudes: list[float | None] = []
    longitudes: list[float | None] = []
    dbhs: list[float | None] = []

    for row in rows:
        common = text(row.get("commonname"))
        # Three sites are marked Alive but named "Stump" or "Proposed Planting
        # Site"; the name is the better witness.
        if row.get("status") not in TREE_STATUSES or is_not_a_tree(common):
            dropped["not a tree"] += 1
            continue
        scientific = text(row.get("scientific"))
        species = normalize_species(SCIENTIFIC_FIXES.get(scientific, scientific))
        if species is None:
            dropped["no species"] += 1
            continue
        tree_ids.append(f"wat-{row['primaryid']}")
        species_list.append(species)
        tree_names.append(normalize_tree_name(common) if common else None)
        latitudes.append(row["latitude"])
        longitudes.append(row["longitude"])
        dbh = row.get("dbh")
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
    table, dropped = build_table(fetch_points(INVENTORY, bbox=BBOX))
    for reason, count in dropped.items():
        if count:
            print(f"Watertown ingest: dropped {count} sites with {reason}", file=sys.stderr)
    table = validate_coordinates(table, city="Watertown", city_code="USBOS")
    table = enforce_tree_schema(table, city="Watertown", data_source="WATERTOWN")
    emit(table)

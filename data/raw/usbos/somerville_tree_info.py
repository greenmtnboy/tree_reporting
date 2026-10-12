#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Somerville MA public trees, from the city's TreeKeeper inventory.

Somerville publishes no tree dataset on its open-data portal; the inventory
lives in Davey's TreeKeeper (https://somervillema.treekeepersoftware.com),
which the Urban Forestry page links as the public map.  Davey re-inventoried
the city in 2024 and staff keep it current, so it is the live record.

TreeKeeper has no export API.  The public map does three things a script can
repeat without a login: select every site inside a polygon into a server-side
"map search" keyed by a client-chosen id, then page the attribute grid over
that selection.  The grid is the same one the map's CSV export reads.  The
GeoServer behind the map serves the points over open WFS, but with only an id
and a site type, so it cannot supply the species.

Somerville borders Cambridge for most of its southern edge, and both cities
inventory the trees on the streets they share.  That overlap is resolved in
`boston_tree_info.preql`, which sees every metro source; this ingest knows
only its own portal.
"""

import json
import re
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (  # noqa: E402
    emit,
    enforce_tree_schema,
    is_not_a_tree,
    normalize_species,
    normalize_tree_name,
    post_json_with_retry,
    validate_coordinates,
)

# ---------------------------------------------------------------------------
# TreeKeeper
# ---------------------------------------------------------------------------
#
# Kept to the four things any TreeKeeper site needs -- host, map layer, grid
# layer, column names -- so a second TreeKeeper city lifts this section into
# shared/platforms/ rather than copying it.

SITE = "https://somervillema.treekeepersoftware.com/"
# `bmlayers_mapid` of the "Smart Tree Inventory" layer, from the map's
# `/map/facilities` listing; the selection is made against it.
MAP_LAYER = "main_layer14"
# Its `bmlayers_facilitytype`, which is what the grid calls `layer_id`.
GRID_LAYER = 14
# Generously around the city (42.373-42.418N, 71.134-71.075W); TreeKeeper
# selects by containment, and every site in this project is Somerville's.
SELECTION_POLYGON = {
    "type": "Polygon",
    "coordinates": [[
        [-71.20, 42.33], [-71.00, 42.33], [-71.00, 42.46], [-71.20, 42.46], [-71.20, 42.33],
    ]],
}
PAGE_SIZE = 5000

# Grid columns, named in the grid's `columnDefs` (`SITE_ATTR*` are per-project).
SPECIES = "SITE_ATTR41"  # "maple: Norway (Acer platanoides)"
DBH_INCHES = "SITE_ATTR25"
INVENTORY_DATE = "SITE_INVENTORYDATE"  # "7/21/2024"


def select_inventory() -> tuple[str, int]:
    """Select every site into a fresh map search; return its id and size.

    The id is the client's to choose -- the map makes a nine-character random
    one -- and the selection lives on the server under it, so no session
    cookie is needed.
    """
    selection = uuid.uuid4().hex[:9]
    payload = post_json_with_retry(
        f"{SITE}cffiles/search.cfc?method=selectByGeometry"
        f"&uid={selection}&geometryType=polygon",
        data=json.dumps(
            {"geom": json.dumps(SELECTION_POLYGON), "layersArray": [MAP_LAYER]}
        ),
        timeout=120,
        backoff=2.0,
    )
    return selection, int(payload["count"])


def fetch_grid(
    selection: str, *, offset: int = 0, limit: int = PAGE_SIZE, sorts: list | None = None
) -> dict:
    query = (
        f"{SITE}cffiles/grids.cfc?method=getTreeKeeperGridOptionsData"
        f"&session_id={selection}&layer_id={GRID_LAYER}&gridType=sites&limit={limit}"
    )
    if offset:
        query += f"&offset={offset}"
    return post_json_with_retry(
        query,
        data=json.dumps({"filters": "[]", "sorts": json.dumps(sorts or [])}),
        timeout=120,
        backoff=2.0,
    )


def fetch_all() -> list[dict]:
    selection, expected = select_inventory()
    rows: list[dict] = []
    while True:
        page = fetch_grid(selection, offset=len(rows)).get("data", [])
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
    if len(rows) != expected:
        # The grid pages over the selection it was given; a short read is a
        # truncated page, not a smaller city.
        raise RuntimeError(
            f"Somerville ingest: selected {expected} sites but the grid returned {len(rows)}"
        )
    return rows


def parse_inventory_date(raw: str | None) -> date | None:
    if not raw:
        return None
    return datetime.strptime(raw, "%m/%d/%Y").date()


def newest_inventory_date() -> date | None:
    """The latest `Inventory Date` on any site, read as one sorted grid row."""
    selection, _ = select_inventory()
    page = fetch_grid(
        selection,
        limit=1,
        sorts=[{"name": INVENTORY_DATE, "desc": True, "priority": 0, "type": 6}],
    ).get("data", [])
    return parse_inventory_date(page[0][INVENTORY_DATE]) if page else None


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


def split_label(label: str | None) -> tuple[str | None, str | None]:
    """`common (Scientific)` -> (common, scientific).

    The scientific name is the LAST top-level parenthesised group, which is
    not the last `(`: Davey's do-not-plant marker is
    `Vacant (Do Not Plant) (Vacant (Do-Not-Plant))`.
    """
    if not label:
        return None, None
    text = label.strip()
    if not text.endswith(")"):
        return text, None
    depth = 0
    for i in range(len(text) - 1, -1, -1):
        if text[i] == ")":
            depth += 1
        elif text[i] == "(":
            depth -= 1
            if depth == 0:
                return text[:i].strip() or None, text[i + 1 : -1].strip() or None
    return text, None


def tree_name_for(common: str | None) -> str | None:
    """TreeKeeper writes common names genus-first with a colon -- `maple:
    Norway`, `cherry/plum: spp.` -- which is the comma inversion
    `normalize_tree_name` already undoes, and `spp.` is not a name."""
    if not common:
        return None
    head, sep, qualifier = common.partition(":")
    qualifier = qualifier.strip()
    if sep and qualifier.lower() in ("spp.", "spp"):
        return normalize_tree_name(head)
    if sep:
        return normalize_tree_name(f"{head.strip()}, {qualifier}")
    return normalize_tree_name(common)


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
    table, dropped = build_table(fetch_all())
    for reason, count in dropped.items():
        if count:
            print(f"Somerville ingest: dropped {count} sites with {reason}", file=sys.stderr)
    table = validate_coordinates(table, city="Somerville", city_code="USBOS")
    table = enforce_tree_schema(table, city="Somerville", data_source="SOMERVILLE")
    emit(table)

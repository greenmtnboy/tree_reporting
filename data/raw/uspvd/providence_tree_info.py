#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Providence, RI's public trees, from the Parks Department Forestry
Division's TreeKeeper inventory.

Licence: none stated.  The City publishes the TreeKeeper map
(https://providenceri.treekeepersoftware.com/, "TreeKeeper 8 System for City
of Providence, RI") with no terms of use, licence or disclaimer, so the source
is displayed with attribution only and is not licensed for reuse.

**Source: the live TreeKeeper inventory, not the Socrata datasets.**  The
City's open-data portal carries two tree tables, both stale copies of the 2006
volunteer street-tree census: "Providence Tree Dataset" (b77h-59tz, 27,250
rows, Open Data Commons Attribution, rows last updated 2021-04, no id column)
and "Providence Tree Inventory" (uv9w-h8i4, last updated 2016-03).  Forestry
keeps the working inventory in Davey TreeKeeper, whose public map is read the
way Somerville's and Belmont's are (`shared.platforms.treekeeper`).  The
project has four layers:

* `main_layer` "Street Trees" (grid layer 4): 30,168 sites on 2026-11-20, the
  current street inventory.  Read.
* `park_sites` "Off ROW" (grid layer 2): 5,920 sites in parks and on public
  property, almost all in Roger Williams Park (South Elmwood).  Read.
* `2017_sites` "Pre-2017 Street Trees" (grid layer 1): 30,590 sites, the
  street inventory before the 2017 re-inventory; the same addresses as the
  current layer under other site ids.  Not read: superseded.
* `tgr` (grid layer 6): 483 points with no species or size.  Not read.

**The id is `SITE_ID`** (`pvd-{SITE_ID}`): TreeKeeper's site number, an
integer on every row and unique across the two layers read (30,168 + 5,920
distinct, no id in both), and the key every TreeKeeper work record hangs on,
so it survives an edit.

**Species is `SITE_ATTR1`**, `Common (Scientific)`; the scientific name is
the last parenthesised group (`split_label`), with cultivars in quotes
(`Prunus serrulata 'Kwanzan'`).  The common name is not read.  TreeKeeper
writes the site's state into the same column: `1 Empty Site`, `1 Empty Pit`,
`Vacant Site, Large/Medium/Small`, `Stump` and `1 Butt` (a felled trunk) are
dropped with a count, and more than `MAX_SITE_SHARE` of the layer dropped
raises, because it would mean the labels changed meaning.  `SPECIES_OVERRIDES`
reads the few values that are not a taxon as written.

**DBH is `SITE_ATTR2`, whole inches** (street trees: median 7, 95th
percentile 25, max 61; park trees median 16, 99th percentile 72).  Empty sites
carry 0 and a few park trees -1; the schema nulls both, and the one 242.

`SITE_ATTR10` is the ward (1-15) and is not mapped.  The public grid exposes
no planting date, inventory date, condition, height or crown spread.
"""

import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (  # noqa: E402
    UNKNOWN_SPECIES,
    emit,
    enforce_tree_schema,
    extract_cultivar,
    is_not_a_tree,
    normalize_species,
    validate_coordinates,
)
from shared.platforms.treekeeper import (  # noqa: E402
    TreeKeeperSite,
    fetch_sites,
    split_label,
)

URL = "https://providenceri.treekeepersoftware.com/"
# Generously around the city (41.772-41.861N, 71.471-71.380W); TreeKeeper
# selects by containment and every site in the project is Providence's.
BBOX = (41.70, 41.95, -71.55, -71.30)

SITES = (
    TreeKeeperSite(URL, map_layer="main_layer", grid_layer=4, bbox=BBOX),  # Street Trees
    TreeKeeperSite(URL, map_layer="park_sites", grid_layer=2, bbox=BBOX),  # Off ROW
)

# Grid columns (`SITE_ATTR*` are per-project).
SPECIES = "SITE_ATTR1"  # "Maple, Norway (Acer platanoides)"
DBH_INCHES = "SITE_ATTR2"

# Scientific names (lowercased) that are a site, a stump or a log rather than
# a tree, besides what `is_not_a_tree` already recognises.
NOT_A_TREE = frozenset({"empty site", "butt"})

# Scientific names (lowercased) that are not a taxon as written: (species, cultivar).
SPECIES_OVERRIDES: dict[str, tuple[str | None, str | None]] = {
    "other spp.": (UNKNOWN_SPECIES, None),
    "unknown spp.": (UNKNOWN_SPECIES, None),
    "unknown tree": (UNKNOWN_SPECIES, None),
    "ulmus x": ("Ulmus", None),  # "Elm, Hybrid"
    "maacki amurense": ("Maackia amurensis", None),
    "cladrastis kentukea (lutea)": ("Cladrastis kentukea", None),
    # "Mimosa (Mimosa julibrissin)": the silk tree, Albizia julibrissin, which
    # the inventory also records under its right name.
    "mimosa julibrissin": ("Albizia julibrissin", None),
}

# 8.2% of the two layers (2,957 of 36,088) was sites, stumps and logs on 2026-11-20.
MAX_SITE_SHARE = 0.3


def parse_species(label) -> tuple[str | None, str | None]:
    _common, scientific = split_label(label)
    if scientific is None:
        return UNKNOWN_SPECIES, None
    key = " ".join(scientific.split()).lower()
    if key in SPECIES_OVERRIDES:
        return SPECIES_OVERRIDES[key]
    return normalize_species(scientific), extract_cultivar(scientific)


def is_site(label) -> bool:
    common, scientific = split_label(label)
    if is_not_a_tree(scientific) or is_not_a_tree(common):
        return True
    return (scientific or "").strip().lower() in NOT_A_TREE


def build_table(rows: list[dict]) -> tuple[pa.Table, Counter]:
    dropped: Counter[str] = Counter()
    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    latitude: list[float] = []
    longitude: list[float] = []
    dbh: list[float | None] = []

    for row in rows:
        label = row.get(SPECIES)
        if is_site(label):
            dropped["empty site, vacant site, stump or log"] += 1
            continue
        if row.get("SITE_ID") is None:
            dropped["no SITE_ID"] += 1
            continue
        lat, lon = row.get("LATITUDE"), row.get("LONGITUDE")
        if lat is None or lon is None:
            dropped["no coordinates"] += 1
            continue
        name, selection = parse_species(label)
        raw_dbh = row.get(DBH_INCHES)
        tree_id.append(f"pvd-{int(row['SITE_ID'])}")
        species.append(name)
        cultivar.append(selection)
        latitude.append(float(lat))
        longitude.append(float(lon))
        dbh.append(float(raw_dbh) if isinstance(raw_dbh, (int, float)) else None)

    n = len(tree_id)
    table = pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["USPVD"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array([None] * n, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )
    return table, dropped


def fetch_rows() -> list[dict]:
    rows: list[dict] = []
    for site in SITES:
        page = fetch_sites(site)
        if not page:
            raise RuntimeError(f"Providence TreeKeeper layer {site.map_layer} returned no sites")
        print(f"Providence: {len(page):,} sites in {site.map_layer}", file=sys.stderr)
        rows.extend(page)
    return rows


if __name__ == "__main__":
    rows = fetch_rows()
    table, dropped = build_table(rows)
    for reason, count in sorted(dropped.items()):
        print(f"Providence: dropped {count:,} rows: {reason}", file=sys.stderr)
    sites = dropped["empty site, vacant site, stump or log"]
    if sites / len(rows) > MAX_SITE_SHARE:
        raise RuntimeError(
            f"Providence: {sites:,} of {len(rows):,} sites are empty, stumps or logs; "
            "the species labels have changed meaning"
        )
    table = validate_coordinates(table, city="Providence", city_code="USPVD")
    table = enforce_tree_schema(table, city="Providence", data_source="PROVIDENCE_OPENDATA")
    emit(table)

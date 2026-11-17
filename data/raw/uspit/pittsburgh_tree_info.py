#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Pittsburgh, PA's city tree inventory, from the WPRDC's CKAN.

Source: "City of Pittsburgh Trees" (`city-trees`) on the Western Pennsylvania
Regional Data Center (data.wprdc.org), published by the City of Pittsburgh
organisation for the Department of Public Works Forestry Division -- "trees
cared for and managed by the City of Pittsburgh" -- under Creative Commons
Attribution (the package's `license_id` is `cc-by`).  The CSV resource
`1515a93c` has an active datastore: 45,709 rows on 2026-11-16, resource last
modified 2022-12-19.  The package itself notes that "the data in this dataset
has not updated since 2020", so this is a snapshot of the Forestry
Division's Davey inventory, not a live feed; the probe is what lets a
re-publish land.

The city's own ArcGIS Online org (YZCmUqbcsUpOKfj7) also carries a
`Tree_Inventory` FeatureServer (42,314 rows, `inv_date` 2005-2022, last data
edit 2025-01-13).  It is not used: the item has no description and no
licence, it is not listed in the city's open-data catalogue, and its rows are
the same vintage (newest inventory date 2022-06) as this resource.

**`id` is the per-tree id**: 45,709 distinct over 45,709 rows, none blank,
checked over the whole resource.  Prefixed `pit-`.

* **`scientific_name` records the site as well as the tree.**  `Vacant Site
  Small/Medium/Large/Not Suitable` (4,192 rows), `Stump` (1,079) and
  `Non-sufficient space` (2) are places, not trees, and are dropped here with
  a logged count; `condition` is `N/A` on nearly all of them, and is not
  itself a filter (61 `Unknown` trees and a few named ones carry it too).
  `Dead` (1,100) and `Unknown` (173) are trees and stay.  103 rows have a
  blank name and publish as `Unknown`.
* The name is already the binomial, written with an `x` for hybrids,
  `spp.` for a genus, an occasional infraspecific rank
  (`Gleditsia triacanthos inermis`) and one quoted cultivar
  (`Malus spp. 'Donald Wyman'`); `enforce_tree_schema` reduces each to
  species rank and moves the cultivar.  `common_name` (`Maple: Norway`) is
  not mapped.
* `diameter_base_height` is the DBH in inches, despite its name: median 10,
  99th percentile 41, maximum 66, and 0 on 328 rows (the schema nulls a zero).
  It is blank on 4,329 rows.
* `height` and `width` are whole feet, 0 on a fifth of rows, and undefined in
  the published data dictionary; `width` is too narrow for a crown spread
  (median 18 ft at 36-41 in DBH) and could be a radius, so `crown_width_m` is
  not emitted.  There is no planting date.
* `latitude`/`longitude` are WGS84 columns; 251 rows have none and are dropped
  by `validate_coordinates`.
"""

import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.ckan import CkanResource, iter_datastore_rows
from shared.ingest import (
    emit,
    enforce_tree_schema,
    is_not_a_tree,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)

RESOURCE = CkanResource(
    "data.wprdc.org", "1515a93c-73e3-4425-9b35-1cd11b2196da", timeout=180
)
FIELDS = "_id,id,scientific_name,diameter_base_height,latitude,longitude"

# Site values the shared `is_not_a_tree` rule does not recognise: a planting
# survey's verdict that the site has no room for a tree.
NOT_A_TREE_EXTRA = frozenset({"non-sufficient space"})


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def as_float(value) -> float | None:
    text = clean(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


class Pittsburgh:
    """Filters and transforms datastore pages, counting what it drops."""

    def __init__(self) -> None:
        self.dropped: Counter[str] = Counter()

    def keep(self, rec: dict) -> bool:
        if clean(rec.get("id")) is None:
            self.dropped["no id"] += 1
            return False
        name = clean(rec.get("scientific_name"))
        if name and (is_not_a_tree(name) or name.lower() in NOT_A_TREE_EXTRA):
            self.dropped[f"site: {name}"] += 1
            return False
        return True

    def __call__(self, rows: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []

        for rec in rows:
            tree_id.append(f"pit-{clean(rec['id'])}")
            species.append(normalize_species(clean(rec.get("scientific_name"))))
            latitude.append(as_float(rec.get("latitude")))
            longitude.append(as_float(rec.get("longitude")))
            dbh.append(as_float(rec.get("diameter_base_height")))

        n = len(tree_id)
        plant_date: list[date | None] = [None] * n
        return pa.table({
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["USPIT"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        })

    def report(self) -> None:
        total = sum(self.dropped.values())
        detail = ", ".join(f"{k} {v}" for k, v in self.dropped.most_common())
        print(f"Pittsburgh: dropped {total} non-tree/unidentified row(s): {detail}", file=sys.stderr)


def iter_row_chunks():
    """Datastore pages, sorted by the datastore's `_id`."""
    return iter_datastore_rows(RESOURCE, fields=FIELDS)


if __name__ == "__main__":
    pittsburgh = Pittsburgh()
    table = stream_to_table(
        iter_row_chunks(), pittsburgh, keep=pittsburgh.keep, label="Pittsburgh OpenData"
    )
    pittsburgh.report()
    table = validate_coordinates(table, city="Pittsburgh", city_code="USPIT")
    table = enforce_tree_schema(
        table, city="Pittsburgh", data_source="PITTSBURGH_OPENDATA"
    )
    emit(table)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Mississauga's city-owned tree inventory, from data.mississauga.ca.

Source: "City Owned Tree Inventory" (`2023_City_Owned_Tree_Inventory`) on the
city's ArcGIS Hub, 499,331 rows -- the largest ArcGIS layer this repo reads.
Paging, the freshness watermark and Esri's epoch-milliseconds live in
`_arcgis_shared`.

**There is no botanical name in this layer, and the column called `BOTNAME` is
the trap.**  It holds a six-letter contraction of the *common* name --
`MANOOO`, `ASGROO`, `LOHOOO`, `SPCOOO` -- built two letters per word from the
inverted English name (MAple NOrway, ASh GReen, LOcust HOney, SPruce
COlorado).  `BOTDESC` is that contraction spelled out (`NORWAY MAPLE`, `ASH
SPP.`), 330 distinct values, and it is the only species information the layer
carries.  `_common_name_species.species_from_common_name` is what turns it
into a taxon; 326 of the 330 values resolve, covering 426,760 of 427,781 rows
that have one.

**Two thirds of the layer is not a living tree, and `SERVSTAT` says which.**
The counts, measured 2026-09-06:

    TREE MAINTAINED BY OPERATIONS      260,043   published
    TREE UNDER WARRANTY                 10,868   published
    DEVELOPER                              142   published
    PRIVATE                                  3   published
    EXPIRED                            152,289   dropped
    FUTURE TREE SITE                    41,481   dropped
    PROPOSED                            16,023   dropped
    EXPIRED NOT IN INVENTORY            10,677   dropped
    NOT TO BE REPLANTED - OBSTRUCTION    4,559   dropped
    TO BE PLANTED                        3,245   dropped
    TO BE DETERMINED                         1   dropped

That leaves 271,056 rows, of which `enforce_tree_schema` drops a further 78
whose species field reads `STUMP`, so the city publishes 270,978 trees with 62
of them unidentified.

`FUTURE TREE SITE`, `PROPOSED`, `TO BE PLANTED` and `NOT TO BE REPLANTED` are
empty planting sites by name -- the first has no diameter on any of its 41,481
rows, and the others carry a nursery caliper (median 5-6 cm against 18 cm for a
maintained tree).  `EXPIRED` is the one worth arguing about, because it is
152,289 rows with a species and a median 14 cm diameter, and "expired" could
plausibly mean an expired *warranty*.  It does not, and the species mix is what
settles it: EXPIRED's four commonest values are GREEN ASH (19,349), ASH SPP.
(16,175), NORWAY MAPLE and WHITE ASH (8,259) -- 29% of the bucket is ash, and
STUMP (5,994) and DEAD (2,665) appear in it and essentially nowhere else, while
the maintained bucket has no ash in its top eleven.  That is the emerald ash
borer, and EXPIRED is a record for a tree that has been removed.  Only 2.1% of
EXPIRED rows share a coordinate with a living one, so these are not replants
either.  Publishing them would put 152k removed trees on the map.

**`UNITID` is a real per-tree key**, checked over the whole layer rather than a
first page: 499,331 distinct values, none null or blank.  `GlobalID` is equally
clean and either would have worked; `UNITID` is the id the city's own asset
system uses.

`LATITUDE`/`LONGITUDE` are published as columns in WGS84, so the geometry is
not requested at all -- 499k geometries is the difference between a comfortable
read and DC's OOM.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, iter_attributes
from _common_name_species import species_from_common_name
from _ingest_shared import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    normalize_tree_name,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services6.arcgis.com/hM5ymMLbxIyWTjn2/arcgis/rest/services/"
    "2023_City_Owned_Tree_Inventory/FeatureServer/0",
    timeout=300,
)

OUT_FIELDS = "UNITID,BOTDESC,DIAM,SERVSTAT,LATITUDE,LONGITUDE"

# The statuses that describe a tree standing in the ground today.  Everything
# else is an empty site or a removed record -- see the module docstring for the
# counts and for how EXPIRED was decided.  Filtered server-side so the removed
# 46% never crosses the wire.
LIVING_STATUSES = (
    "TREE MAINTAINED BY OPERATIONS",
    "TREE UNDER WARRANTY",
    "DEVELOPER",
    "PRIVATE",
)
WHERE = "SERVSTAT IN (" + ", ".join(f"'{s}'" for s in LIVING_STATUSES) + ")"


def iter_row_chunks():
    """One ArcGIS page at a time; the layer's own maxRecordCount is 2000."""
    return iter_attributes(LAYER, out_fields=OUT_FIELDS, where=WHERE)


def transform(rows: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    tree_name: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for rec in rows:
        raw_id = str(rec.get("UNITID") or "").strip()
        if not raw_id:
            continue
        common = rec.get("BOTDESC")
        tree_id.append(f"mis-{raw_id}")
        species.append(species_from_common_name(common))
        # The common name is kept on the row as well as resolved: it is what
        # the city wrote, and the tree card shows it above the binomial.
        tree_name.append(normalize_tree_name(common))
        latitude.append(rec.get("LATITUDE"))
        longitude.append(rec.get("LONGITUDE"))
        # Every Canadian portal publishes diameter in centimetres.
        dbh.append(cm_to_inches(rec.get("DIAM")))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CAMIS"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "tree_name": pa.array(tree_name, type=pa.string()),
            "plant_date": pa.array([None] * len(tree_id), type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(
        iter_row_chunks(), transform, label="Mississauga OpenData"
    )
    table = validate_coordinates(table, city="Mississauga", city_code="CAMIS")
    table = enforce_tree_schema(
        table, city="Mississauga", data_source="MISSISSAUGA_OPENDATA"
    )
    emit(table)

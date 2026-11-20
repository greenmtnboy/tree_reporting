#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Waterloo's (Ontario) city tree inventory, from the City of Waterloo's open data.

Source: "Street Tree Inventory" (item 2447415303e74bb9acdf0f43c2236b72, owned
by the City's `OpenData_Waterloo` account, listed on https://data.waterloo.ca).
The item carries no licence field of its own; the portal's licence page
(https://data.waterloo.ca/pages/open-data-licence) puts everything on the site
under the Waterloo Open Data User Licence (adapted from the UK OGL): copy,
adapt and use commercially; credit is not required, and if given reads
"Contains information provided by the City of Waterloo under licence".

**Despite its name the layer holds every City tree**: `TREE_TYPE` is Street
(32,328 kept rows), Park (14,190), Cemetery (871), Facility (161).  There is
no separate park-tree layer.

**Only `STATUS` Existing and To Be Removed are trees in the ground.**
Measured 2026-11-19 over 61,321 rows: Existing 47,186, Stumped 11,764,
Proposed 1,331, To Be Stumped 649, To Be Removed 366, Peg 15 (a planting
peg), Stump 10.  To Be Stumped carries a `REMOVED_DATE` on 645 of 649 rows --
the tree is down and the stump waits for grinding -- so it is dropped with
the stumps and the planting plans; To Be Removed is a standing tree slated
for removal and is kept.  The filter is server-side.  196 Existing rows carry
a `REMOVED_DATE` (143 of them in 2026, mostly with no reason); the status is
the publisher's statement and is trusted.

**The id is `ASSET_ID`** ("Tree ID"; `waterloo-{id}`): the layer has no
GlobalID, and `ASSET_ID` was non-null and unique over all 61,321 rows
(checked 2026-11-19).  `enforce_tree_schema` still raises on a repeat or a
null; a row without one is dropped here with a count.  (`wat-` is
Watertown's prefix.)

**Species is `LATIN_NAME`**, free text with no domain.  The shared schema
handles its habits: `Ulmus var` / `Pyrus var` (genus, variety unrecorded) to
the genus, a trailing `columnar` dropped, quoted cultivars split off,
`Quercus robar` and `Liquidamber` corrected.  `unknown` publishes as Unknown.
`COM_NAME` is the common name and is never mapped.

**DBH is centimetres** (`DBH_CM`; median 5 over the 19.6k measured kept rows,
p90 36, p99 76 -- recently planted trees are measured at planting).  Zero is
"not measured" and is nulled; 2,023 (a year keyed into the field) falls to the
schema's 200-inch ceiling.

No planting date or crown spread is published.  Geometry is requested in WGS84.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services.arcgis.com/ZpeBVw5o1kjit7LT/arcgis/rest/services/"
    "Street_Tree_Inventory/FeatureServer/0",
    timeout=300,
)
OUT_FIELDS = "OBJECTID,ASSET_ID,LATIN_NAME,DBH_CM"
WHERE = "STATUS IN ('Existing', 'To Be Removed')"


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def diameter_inches(value) -> float | None:
    if value is None:
        return None
    number = float(value)
    return cm_to_inches(number) if number > 0 else None


def asset_id(rec: dict) -> int | None:
    value = rec.get("ASSET_ID")
    return None if value is None else int(value)


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        tree_id.append(f"waterloo-{asset_id(rec)}")
        species.append(clean(rec.get("LATIN_NAME")))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(diameter_inches(rec.get("DBH_CM")))

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CAWAT"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array([None] * n, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


_unidentified = 0


def has_id(feature: dict) -> bool:
    global _unidentified
    if asset_id(feature.get("attributes") or {}) is None:
        _unidentified += 1
        return False
    return True


if __name__ == "__main__":
    pages = iter_features(
        LAYER,
        out_fields=OUT_FIELDS,
        where=WHERE,
        return_geometry=True,
        extra_params={"geometryPrecision": "7"},
    )
    table = stream_to_table(pages, transform, keep=has_id, label="Waterloo OpenData")
    if _unidentified:
        print(
            f"Waterloo ingest: dropped {_unidentified:,} rows with no ASSET_ID",
            file=sys.stderr,
        )
    table = validate_coordinates(table, city="Waterloo", city_code="CAWAT")
    table = enforce_tree_schema(
        table, city="Waterloo", data_source="WATERLOO_OPENDATA"
    )
    emit(table)

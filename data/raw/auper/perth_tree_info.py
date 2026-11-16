#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Perth's municipal tree inventory, from the City of Perth's ArcGIS Online.

Source: "Trees in the City" (item c5ce51d9789a4e9a8510acb8c8f3ecf4, owner
GIS.SERVICES_COP, CC BY 4.0), the `PKS_AST_TREESMASTER_PV` hosted view, layer
0: "public trees inventoried by the City of Perth ... from field capture from
our parks team".  Layer paging, the freshness watermark and Esri's
epoch-milliseconds all live in `shared.platforms.arcgis`.

Measured 2026-11-16 (layer `dataLastEditDate` 2026-10-31): 17,118 points, all
with a geometry, spanning -31.9900 to -31.9421 and 115.8109 to 115.8878 (the
CBD, Northbridge, East and West Perth, Crawley and the Nedlands strip the city
took over in 2016).  `RETIRED` is null on every row; it is still honoured as a
filter so a retired tree the city starts flagging never reaches the map.
17,111 rows publish: 3 repeated ids (below) and 4 Crawley foreshore trees
(Matilda Bay, ids 12504-12507) that sit up to 4 m south of the -31.99 edge of
AUPER's CITY_BOUNDS box and are dropped by `validate_coordinates`.

Field choices:

  * `tree_id` <- `TREE_ID`, the parks team's asset number.  The view exposes
    no `GlobalID` (the layer names one but returns no such field), so the
    asset number is the publisher's own id.  It is non-null everywhere and
    unique except for two ids: 54950 (two Chinese elms 3 m apart, identical
    attributes) and 58065 (three Chinese elms planted June 2026).  Those are
    data-entry repeats of one asset number, and the grain is the asset, so
    the first record by OBJECTID is kept and the 3 repeats are dropped with a
    logged count; above `MAX_DUPLICATE_FRACTION` the run refuses instead.
    `OBJECTID` was not chosen: a hosted view's OBJECTIDs renumber when the
    view is overwritten, which would re-key every tree.
  * `species` <- `BOTANICAL_NAME`, already the scientific name with quoted
    cultivars ("Platanus x acerifolia 'Liberty'").  57 rows are null and 7
    say "Unknown species"; both publish as `Unknown`.  A formula hybrid,
    "Brachychiton populneus x acerifolius" (77 trees), names two parents and
    is neither: the shared rule would truncate it to the first parent, so it
    is reduced to its genus here (`FORMULA_HYBRID`).  `COMMON_NAME` is not
    mapped (common names come from enrichment).
  * `plant_date` <- `DATE_PLANTED`, with two placeholders nulled.  11,381 rows
    (66%) carry 2019-05-10, stamped minutes apart, which is the date the
    inventory was loaded rather than when anything was planted
    (`PLACEHOLDER_PLANT_DATE`).  43 more fall before 1901 (1753-01-01,
    1800-01-01 and scattered 1900 timestamps with clock times), which are
    typed-in placeholders, not Victorian plantings.  About 5,300 credible
    dates remain, mostly 2007-2026.
  * `diameter_at_breast_height` is null: the layer records no trunk size.
    `TREE_HEIGHT` (filled on 14% of rows, almost all young trees) is not part
    of the canonical schema.
  * `latitude`/`longitude` from the point geometry, reprojected server-side
    from Web Mercator with `outSR=4326`.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_date, esri_point, iter_features
from shared.ingest import (
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

# Kept in step with perth_update_time.py.
LAYER = FeatureLayer(
    "https://services7.arcgis.com/v8XBa2naYNQGOjlG/arcgis/rest/services/"
    "PKS_AST_TREESMASTER_PV/FeatureServer/0"
)
SOURCE_URL = LAYER.url

OUT_FIELDS = "OBJECTID,TREE_ID,BOTANICAL_NAME,DATE_PLANTED,RETIRED"

# The inventory load date, stamped on two thirds of the layer.
PLACEHOLDER_PLANT_DATE = date(2019, 5, 10)
# Dates before this are placeholders (1753, 1800, 1900 with clock times).
EARLIEST_PLANT_DATE = date(1901, 1, 1)

# Above this share of rows repeating an earlier TREE_ID the run refuses to
# publish: a handful of re-used asset numbers is data entry, a tenth of the
# layer is the id having changed meaning.
MAX_DUPLICATE_FRACTION = 0.01

_seen_ids: set[int] = set()
_counts = {"records": 0, "duplicates": 0, "retired": 0, "no_id": 0}


def species_name(value: str | None) -> str | None:
    """`BOTANICAL_NAME` with a formula hybrid reduced to its genus.

    "Brachychiton populneus x acerifolius" is Genus epithet x epithet: a cross
    of two named parents.  Everything else (including the hybrid binomials
    "Platanus x acerifolia", "Erythrina x sykesii") passes through untouched for
    `enforce_tree_schema` to sanitise.
    """
    if value is None:
        return None
    tokens = value.split()
    if (
        len(tokens) == 4
        and tokens[2].lower() in ("x", "×")
        and tokens[1].islower()
        and tokens[3].islower()
    ):
        return tokens[0]
    return value


def plant_date_of(value) -> date | None:
    planted = esri_ms_to_date(value)
    if planted is None or planted == PLACEHOLDER_PLANT_DATE or planted < EARLIEST_PLANT_DATE:
        return None
    return planted


def keep_record(feature: dict) -> bool:
    """Drop retired trees, rows without an id and repeats of an earlier id.

    Pages arrive in OBJECTID order, so the record kept for a repeated id is the
    one with the lowest OBJECTID.
    """
    attrs = feature.get("attributes") or {}
    _counts["records"] += 1
    if attrs.get("RETIRED") is not None:
        _counts["retired"] += 1
        return False
    tree_id = attrs.get("TREE_ID")
    if tree_id is None:
        _counts["no_id"] += 1
        return False
    if tree_id in _seen_ids:
        _counts["duplicates"] += 1
        return False
    _seen_ids.add(tree_id)
    return True


def iter_row_chunks():
    """One ArcGIS page at a time, with WGS84 point geometry."""
    return iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, order_by="OBJECTID"
    )


def transform(rows: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in rows:
        rec = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        tree_id.append(f"per-{int(rec['TREE_ID'])}")
        species.append(species_name(rec.get("BOTANICAL_NAME")))
        plant_date.append(plant_date_of(rec.get("DATE_PLANTED")))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(None)

    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["AUPER"] * len(tree_id), type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "plant_date": pa.array(plant_date, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


def report_dropped() -> None:
    records = _counts["records"] or 1
    for what in ("retired", "no_id", "duplicates"):
        if _counts[what]:
            print(f"Perth ingest: dropped {_counts[what]} {what} record(s)", file=sys.stderr)
    if _counts["duplicates"] / records > MAX_DUPLICATE_FRACTION:
        raise RuntimeError(
            f"Perth ingest: {_counts['duplicates']} of {records} records repeat "
            "an earlier TREE_ID; it no longer identifies a tree"
        )


if __name__ == "__main__":
    table = stream_to_table(
        iter_row_chunks(), transform, keep=keep_record, label="Perth OpenData"
    )
    report_dropped()
    table = validate_coordinates(table, city="Perth", city_code="AUPER")
    table = enforce_tree_schema(
        table, city="Perth", data_source="PERTH_OPENDATA"
    )
    emit(table)

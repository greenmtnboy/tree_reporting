#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Denver's municipal tree inventory, from the city's ArcGIS Hub.

Source: "Parks, Medians, and Parkway Trees" (`ODC_PARK_TREEINVENTORY_P`),
359,263 rows on the Denver geospatial hub.  Layer paging, the freshness
watermark and Esri's epoch-milliseconds all live in `_arcgis_shared`.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, iter_attributes
from _ingest_shared import (
    emit,
    enforce_tree_schema,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/"
    "ODC_PARK_TREEINVENTORY_P/FeatureServer/241"
)

# The layer publishes WGS84 lat/lon as ordinary columns, so the geometry is
# redundant weight -- read the attributes and skip it.
OUT_FIELDS = "SITE_ID,SPECIES_BOTANIC,SPECIES_COMMON,DIAMETER,X_LONG,Y_LAT"

# Denver marks a planting site with no tree in it by prefixing the species with
# an underscore: "_Vacant Site", "_Vacant site - new", "_Vacant site-not
# plantable", "_Stump not plantable".  41,433 of the 359,263 rows (11.5%) are
# one of those, and they are not trees -- rendering them would put a dot on the
# map for every empty tree lawn in the city.
#
# Matched on the prefix rather than on the four spellings seen today, because
# the prefix is the city's own convention and a fifth spelling of "nothing here"
# should be caught the same way.  Filtered here rather than in the ArcGIS
# `where` clause: `_` is a single-character wildcard in ArcGIS's LIKE dialect,
# so the server-side form of this needs escaping that varies by backend, and
# the saving is 11% of a transfer that is paged anyway.
VACANT_SITE_PREFIX = "_"

# Values that are notes rather than a species or a placeholder.  `test site` is
# a single row someone left behind.
NON_SPECIES_VALUES = frozenset({"test site"})

# Denver publishes DBH as a six-inch bucket, not a measurement.  The midpoint is
# the honest reconstruction of a bucketed value; the open-topped bucket gets its
# lower bound plus half the common width, which is the same rule applied to a
# bucket whose upper bound we do not know.
#
# The alternative -- leaving dbh null for every Denver tree -- would drop the
# city out of every size chart on the summary page.  A midpoint is wrong by up
# to three inches per tree and right in aggregate, which is what those charts
# ask of it.
DIAMETER_BUCKET_INCHES: dict[str, float] = {
    "0 to 6": 3.0,
    "6 to 12": 9.0,
    "12 to 18": 15.0,
    "18 to 24": 21.0,
    "24 to 30": 27.0,
    "30 to 36": 33.0,
    "36 to 42": 39.0,
    "42 to 48": 45.0,
    "48 +": 51.0,
}


def parse_diameter(value: str | None) -> float | None:
    """A DIAMETER bucket label to its midpoint in inches, or None.

    Unknown labels return None rather than raising: a new bucket spelling is a
    tree we cannot size, not a reason to fail the city's whole refresh.
    """
    if not value:
        return None
    return DIAMETER_BUCKET_INCHES.get(value.strip())


def is_planted_site(record: dict) -> bool:
    """False for Denver's empty-site and stump markers.

    Passed to `stream_to_table` as its `keep` filter rather than applied inside
    `transform`, so the dropped rows never occupy a column and the run reports
    how many it dropped.
    """
    species_raw = record.get("SPECIES_BOTANIC")
    if not species_raw:
        return True  # No species recorded is still a tree; sanitize_species decides.
    stripped = species_raw.strip()
    if stripped.startswith(VACANT_SITE_PREFIX):
        return False
    return stripped.lower() not in NON_SPECIES_VALUES


def normalize_common_name(value: str | None) -> str | None:
    """Denver writes common names inverted for sorting: "Pear, Flowering".

    Un-invert the single-comma form so the map's tree card reads "Flowering
    Pear".  Anything with a different shape is passed through as written.
    """
    if not value:
        return None
    text = " ".join(value.split())
    if not text or text.lower() in {"n/a", "na", "unknown"}:
        return None
    head, sep, tail = text.partition(",")
    if sep and "," not in tail:
        head, tail = head.strip(), tail.strip()
        if head and tail:
            return f"{tail} {head}"
    return text


def iter_row_chunks():
    """One ArcGIS page at a time.

    A generator, not a bulk read: 359k features held simultaneously as a
    response body, a dict each and a Python list per column is what OOM-killed
    Washington DC's 2 GiB container at 216k.
    """
    return iter_attributes(LAYER, out_fields=OUT_FIELDS)


def transform(rows: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    tree_name: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for rec in rows:
        site_id = rec.get("SITE_ID")
        site_id = str(site_id).strip() if site_id is not None else ""
        if not site_id:
            continue

        tree_id.append(f"den-{site_id}")
        species.append(normalize_species(rec.get("SPECIES_BOTANIC")))
        tree_name.append(normalize_common_name(rec.get("SPECIES_COMMON")))
        # The layer carries INVENTORY_DATE -- when a surveyor visited -- and no
        # planting date.  Those are different facts and only one of them is
        # `plant_date`, so Denver publishes none.
        plant_date.append(None)
        latitude.append(rec.get("Y_LAT"))
        longitude.append(rec.get("X_LONG"))
        dbh.append(parse_diameter(rec.get("DIAMETER")))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["USDEN"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "tree_name": pa.array(tree_name, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(
        iter_row_chunks(), transform, keep=is_planted_site, label="Denver OpenData"
    )
    table = validate_coordinates(table, city="Denver", city_code="USDEN")
    table = enforce_tree_schema(
        table,
        city="Denver",
        data_source="DENVER_OPENDATA",
        unique_tree_ids=True,
    )
    emit(table)

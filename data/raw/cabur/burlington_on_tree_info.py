#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Burlington, Ontario's city-owned tree inventory, from Navigate Burlington.

Source: "City Owned Trees" (`COB/Urban_Forestry/MapServer/0`) on the city's
on-prem ArcGIS Server, catalogued at `navburl-burlington.opendata.arcgis.com`,
80,287 rows.  Paging lives in `_arcgis_shared`.

**Not Burlington, Vermont**, which is already on the map as `USBTV`.  Both
cities are called Burlington and both publish a tree inventory on ArcGIS; this
one is `CABUR`, and the display name carries the province so the city picker
cannot show two identical buttons.

**The species column is an inverted common name and nothing else.**
`SPECIES_COMMONNAME` holds `MAPLE - NORWAY`, `BUCKEYE- OHIO`, `CEDAR - EASTERN
WHITE` -- 192 distinct values, no botanical name anywhere in the layer.
`_common_name_species` resolves 187 of them, covering 79,763 of the 79,877 rows
that carry one; the residue is `UNKNOWN`, `TO BE UPDATED` and three rows whose
value is a number.  Note that the hyphen is what inverts the name and the
un-inversion is not universal in this column -- `HORSE-CHESTNUT`,
`MOUNTAIN-ASH` and `BLUE-BEECH` are single hyphenated names, which is why
`common_name_key` only treats a *spaced* hyphen as a separator.

**`STATUS` decides what is a tree**, and `ASSET_STATUS` -- which has a domain
listing `Retired` and `Planned` -- is null on all 80,287 rows and says nothing:

    Alive                                 67,720   published
    Juvenile Tree (+ Dead, + Monitor)      5,446   published
    Warranty Tree                          4,942   published
    Dead                                   1,170   published
    Stump                                    546   dropped
    Potential Planting (2 spellings)         282   dropped
    Proposed Planting - with Contractor      181   dropped

A dead tree and a dead juvenile are trees standing at that spot and stay; a
stump and a planting that has not happened are not.  That is the rule
`is_not_a_tree` applies everywhere else, applied here through the status column
because this source records it there rather than in the species field.

**The layer holds a handful of impossible coordinates.**  Its published extent
reaches latitude -48 and +88, which is not Ontario; `validate_coordinates`
drops whatever falls outside `CITY_BOUNDS` and reports the count.  There are no
lat/lon columns, so geometry is requested with `out_sr=4326` and reprojected
server-side out of NAD83 / UTM 17N.

`PRIMARYID` ("Entity Number") is unique and non-null across the whole layer --
checked over all 80,287 rows -- and is the city's own asset number rather than
a row number, so it is preferred to `OBJECTID`.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, esri_point, iter_features
from _common_name_species import species_from_common_name
from _ingest_shared import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    normalize_tree_name,
    parse_plant_date_year,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://mapping.burlington.ca/arcgisweb/rest/services/COB/"
    "Urban_Forestry/MapServer/0",
    timeout=300,
)

OUT_FIELDS = "PRIMARYID,SPECIES_COMMONNAME,DBH_CM,YEAR_PLANTED,STATUS"

# The statuses that are not a standing tree.  Written as an exclusion rather
# than an inclusion list because the "Juvenile Tree - ..." family has three
# spellings and a fourth would otherwise be dropped silently.
NOT_A_TREE_STATUSES = (
    "Stump",
    "Potential Planting for Consideration",
    "Potential Planting- on Hold",
    "Proposed Planting - with Contractor",
)
WHERE = "STATUS NOT IN (" + ", ".join(f"'{s}'" for s in NOT_A_TREE_STATUSES) + ")"


def iter_row_chunks():
    """One ArcGIS page at a time; this MapServer caps a page at 1000."""
    return iter_features(
        LAYER,
        out_fields=OUT_FIELDS,
        where=WHERE,
        return_geometry=True,
        out_sr=4326,
    )


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    tree_name: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        geom = feature.get("geometry") or {}
        raw_id = rec.get("PRIMARYID")
        if raw_id in (None, ""):
            continue
        common = rec.get("SPECIES_COMMONNAME")

        tree_id.append(f"bur-{raw_id}")
        species.append(species_from_common_name(common))
        tree_name.append(normalize_tree_name(common))
        plant_date.append(parse_plant_date_year(rec.get("YEAR_PLANTED")))
        lat, lon = esri_point(geom)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(cm_to_inches(rec.get("DBH_CM")))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CABUR"] * len(tree_id), type=pa.string()),
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
        iter_row_chunks(), transform, label="Burlington ON OpenData"
    )
    table = validate_coordinates(table, city="Burlington ON", city_code="CABUR")
    table = enforce_tree_schema(
        table, city="Burlington ON", data_source="BURLINGTON_ON_OPENDATA"
    )
    emit(table)

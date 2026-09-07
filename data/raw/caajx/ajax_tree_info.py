#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Ajax's town-owned tree inventory, from opendata.ajax.ca.

Source: "Town Trees" (`Ajax_Open_Data/MapServer/8`) on the town's on-prem
ArcGIS Server, 53,848 rows.  Paging lives in `_arcgis_shared`.

**Two columns name the species and neither is a binomial.**  `SPCODE` holds a
USDA-PLANTS-style symbol (`TIAM`, `FAGR`, `ACGI`) and carries a coded-value
domain naming each in English; `TYPE` holds the English name directly.  The
code is read first and the free-text column second, because the domain is the
town's own dictionary while `TYPE` is a 10-character field that truncates
whatever does not fit -- `Accolade E`, `common Hac`, `Geenspire`, `ohio bucke`.
Of the 53,848 rows, 44,591 carry a code the domain names and 8,180 fall back to
`TYPE`; 1,077 have neither and publish as `Unknown`.

The English name then goes through `_common_name_species`, exactly as
Mississauga's and Burlington's do.  Decoding the *symbol* is deliberately not
attempted even though most of them look like USDA symbols: `TIAM` is
convincingly *Tilia americana* and `MAAM9` is not convincingly anything, and a
symbol table would be a guess at a taxon where the domain has already published
the answer in English.

**`STATUS` decides what is a tree.**  `TREE` (44,997), `DEDICATION TREE` (830),
`PRIVATE` (97) and 456 rows with no status are published; `REMOVED` (7,444),
`TRUNK TO BE REMOVED` (17), `TO BE REMOVED WOODLOT` (4), `TO BE STUMPED` (2)
and `TO BE PLANTED` (1) are not standing trees and are dropped.

**`DBH` is centimetres**, which is worth stating because the column carries no
unit and the layer's overall median of 8 makes it look like inches.  It is not:
broken down by species the median Norway maple is 25 and the median silver
maple 33, which are ordinary centimetre diameters, where as inches they would
be 64 cm and 84 cm medians.  The low overall median is the town's large
population of recently planted saplings -- the median sugar maple is 6.

`ASSET_ID` is the key (`TREE039547`): 53,848 distinct values, none null,
checked over the whole layer.  `FACILITYID` is the trap here, as it was in
Washington DC -- 7,203 rows have none and eight share one.  `GPS_LAT`/`GPS_LON`
exist as strings but only on 44,695 rows, so the geometry is read instead and
reprojected server-side out of NAD83 / UTM 17N.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import (
    FeatureLayer,
    coded_value_domain,
    esri_ms_to_date,
    esri_point,
    iter_features,
)
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
    "https://ajaxmaps.ajax.ca/gisernie/rest/services/Public/"
    "Ajax_Open_Data/MapServer/8",
    timeout=300,
)

OUT_FIELDS = "ASSET_ID,SPCODE,TYPE,DBH,INSTALLDATE,STATUS"

# The statuses that are not a standing tree.  A blank status is kept: 456 rows
# have one and nothing says they are gone.
NOT_A_TREE_STATUSES = (
    "REMOVED",
    "TRUNK TO BE REMOVED",
    "TO BE REMOVED WOODLOT",
    "TO BE REMOVED PARKS",
    "TO BE REMOVED STREET",
    "TO BE STUMPED",
    "TO BE PLANTED",
    "TO BE PLANTED RESTORATION",
)
WHERE = (
    "STATUS IS NULL OR STATUS NOT IN ("
    + ", ".join(f"'{s}'" for s in NOT_A_TREE_STATUSES)
    + ")"
)

COMMON_NAME_BY_CODE = coded_value_domain(LAYER, "SPCODE")


def iter_row_chunks():
    """One ArcGIS page at a time; the layer's own maxRecordCount is 2000."""
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
        raw_id = str(rec.get("ASSET_ID") or "").strip()
        if not raw_id:
            continue
        code = str(rec.get("SPCODE") or "").strip()
        common = COMMON_NAME_BY_CODE.get(code) or rec.get("TYPE")

        tree_id.append(f"ajx-{raw_id}")
        species.append(species_from_common_name(common))
        tree_name.append(normalize_tree_name(common))
        plant_date.append(esri_ms_to_date(rec.get("INSTALLDATE")))
        lat, lon = esri_point(geom)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(cm_to_inches(rec.get("DBH")))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CAAJX"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "tree_name": pa.array(tree_name, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Ajax OpenData")
    table = validate_coordinates(table, city="Ajax", city_code="CAAJX")
    table = enforce_tree_schema(table, city="Ajax", data_source="AJAX_OPENDATA")
    emit(table)

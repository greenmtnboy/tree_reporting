#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Guelph's (Ontario) city tree inventory, from the City of Guelph's open data.

Source: "Trees" (item 20b0f47ee4ed421d90b78d91fc220c3e, owned by the City's
`GuelphGIS_cityofguelph` account and listed on the Guelph GeoDataHub,
https://explore.guelph.ca), served from the City's own ArcGIS Server as
`OpenData/OpenData1` layer 7.  The item's licence field links the City of
Guelph Open Data Licence v2.0 (https://explore.guelph.ca/pages/open-data-license),
the Open Government Licence - Canada 2.0 with the City substituted: copy,
modify, publish and use commercially, acknowledging the source; with no
attribution statement of the provider's own, it reads "Contains information
licensed under the Open Government Licence - City of Guelph."

The catalogue lead, `Forestry/StreetTreeOwnership/MapServer/0` (item
9388866e..., "City Trees"), is the same inventory as the City's internal
map service: same `ASSET_ID`s, 106 more rows (`TREE_STATUS` REMOVAL - TREE),
no GlobalID, no planting date and no licence of its own.  The open data layer
is the published copy and is the one read here.

**The layer is the City's street-tree inventory**: every row is
`TREE_STATUS` ACTIVE (57,941 rows on 2026-11-20; removed and proposed trees
are not published).  `OWNER` is CITY (32.6k), PRIVATE (14.7k), BOUNDARY
(10.3k, on the property line) and UNKNOWN (237): the City inventories the
private trees along its streets too, and all are trees in the ground, so all
are kept.  It is not a park or woodlot inventory.

**The id is `GlobalID`** (`guelph-{guid}`): non-null and unique over all
57,941 rows (checked 2026-11-20), and the layer is edited in place (OBJECTID
runs to 74k with 58k rows, in `ASSET_ID` order).  `ASSET_ID` (`FT10001`, the
forestry asset number) is also unique and non-null and would be the fallback.
A row without a GlobalID is dropped with a count.

**Species is `SPECIES`, a coded-value domain of `LATIN (COMMON)` strings**
(`ACER PLATANOIDES (CRIMSON KING MAPLE)`); the Latin part is the text before
the parenthesis.  `SPECIES_SCIENTIFIC` / `SPECIES_ID` look like a stale join
of the same list -- null on 360 rows whose `SPECIES` is set (all of
`OSTRYA VIRGINIANA` and `PRUNUS SERRULATA 'KWANZAN'`) and disagreeing with it
on 117 more -- and are not read.  Cultivars appear only in the parenthesised
common name (`CRIMSON KING MAPLE`, `GREEN MOUNTAIN MAPLE`) except two quoted in
the Latin part (`'KWANZAN'`, `ACER X 'NORWEGIAN SUNSET'`), which the schema
splits; the common names are never mapped.  `UNKNOWN (UNKNOWN)` and
`OTHER SPECIES (OTHER)` publish as Unknown, `X SPECIES` as the genus.

`CONIUM MACULATUM (HEMLOCK)` (38 rows) is poison hemlock, a herb: the domain
entry was keyed against the wrong "hemlock".  The tree is Tsuga (eastern
hemlock, Tsuga canadensis, is the only one native here, but the domain does
not say which), so it publishes as the genus (`MISKEYED_SPECIES`).

**DBH is centimetres** (`DBH`, integer; median 19, p10 4, p90 53, p99 90).
Zero is "not measured" and is nulled; 628 and 879 fall to the schema's
200-inch ceiling.

**`DT_PLANTED`** is set on 9k rows, 1998-2026; there is no load-default date
(the most common day, 2006-05-02, holds 254 rows of a spring planting).  Dates
are stored either at midnight UTC or at local midnight / a daytime local
stamp (04:00-23:00Z), so the UTC date is the local date.  `CROWN_AREA` is
empty on every row.  Geometry is stored in UTM 17N (EPSG:26917) and requested
in WGS84.  A single 100k-row request (the layer's maxRecordCount) fails with
HTTP 500, so pages are 5,000 rows.
"""

import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_ms_to_date,
    esri_point,
    iter_features,
)
from shared.ingest import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://gismaps.guelph.ca/hosting/rest/services/OpenData/OpenData1/"
    "FeatureServer/7",
    timeout=300,
)
OUT_FIELDS = "OBJECTID,GlobalID,SPECIES,DBH,DT_PLANTED,TREE_STATUS"
WHERE = "TREE_STATUS = 'ACTIVE'"
PAGE_SIZE = 5000

# Domain entries whose Latin half names the wrong organism.
MISKEYED_SPECIES = {
    "CONIUM MACULATUM": "Tsuga",
}

_COMMON_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def latin_name(value) -> str | None:
    """`ACER PLATANOIDES (NORWAY MAPLE)` -> `ACER PLATANOIDES`."""
    text = clean(value)
    if text is None:
        return None
    latin = clean(_COMMON_SUFFIX.sub("", text))
    if latin is None:
        return None
    return MISKEYED_SPECIES.get(latin.upper(), latin)


def diameter_inches(value) -> float | None:
    if value is None:
        return None
    number = float(value)
    return cm_to_inches(number) if number > 0 else None


def global_id(rec: dict) -> str | None:
    value = clean(rec.get("GlobalID"))
    return value.strip("{}").lower() if value else None


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    planted: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        tree_id.append(f"guelph-{global_id(rec)}")
        species.append(latin_name(rec.get("SPECIES")))
        planted.append(esri_ms_to_date(rec.get("DT_PLANTED")))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(diameter_inches(rec.get("DBH")))

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CAGLP"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array(planted, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


_unidentified = 0


def has_id(feature: dict) -> bool:
    global _unidentified
    if global_id(feature.get("attributes") or {}) is None:
        _unidentified += 1
        return False
    return True


if __name__ == "__main__":
    pages = iter_features(
        LAYER,
        out_fields=OUT_FIELDS,
        where=WHERE,
        return_geometry=True,
        page_size=PAGE_SIZE,
        extra_params={"geometryPrecision": "7"},
    )
    table = stream_to_table(pages, transform, keep=has_id, label="Guelph OpenData")
    if _unidentified:
        print(
            f"Guelph ingest: dropped {_unidentified:,} rows with no GlobalID",
            file=sys.stderr,
        )
    table = validate_coordinates(table, city="Guelph", city_code="CAGLP")
    table = enforce_tree_schema(
        table, city="Guelph", data_source="GUELPH_OPENDATA"
    )
    emit(table)

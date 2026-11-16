#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Naperville, IL's right-of-way tree inventory, from the city's ArcGIS Online.

Source: "Right of Way Tree Inventory" (ArcGIS item
9ee23fb99f1d4391824b66fddc2f10f1, owner `Naperville_GIS`, licence CC0,
copyright "City of Naperville"), the hosted layer
`ROW_Tree_Inventory/FeatureServer/0` (`Row_Tree`): trees along the city's
rights-of-way, maintained by the Naperville Parks Department (Forestry).
95,506 rows on 2026-11-16; the layer's `dataLastEditDate` was 2026-05-07.

**Only standing trees are published, and `STATUS` says which.**  Its
coded-value domain is 1 Active, 2 Site, 3 Removal, 4 Private-Active,
5 Private-Removal, 6 Unincorporated; the layer uses two of them:

    1 Active      72,360   published, less 10 SITE/STUMP rows (below)
    3 Removal     23,146   dropped -- removed trees, kept as history:
                           17,992 carry a `DateRemoved` and the other 5,154
                           were back-filled in 2026 with the comment
                           "UNKNOWN REMOVAL DATE"

The domain is read at run time and `Active`/`Private-Active` are kept by
name, so a renumbered domain cannot silently publish removals.  Among the
active rows, `ROWTREE_TYPE` also marks 8 `SITE` and 2 `STUMP` rows (and the
domain has `NO SITE` and `ANY TREE - LET ARBORIST DECIDE`, a planting
request), which are dropped here; 117 `SHRUB` rows keep the `Shrub` sentinel
and 146 `UNKNOWN` plus 64 blank rows are trees of unknown species.

**`GlobalID` is the key** (`npv-{guid}`, lower-case): the layer's
`globalIdField`, 95,506 distinct, none null.  `FACILITYID` is equally unique
today (the city's numeric asset id) but the runbook prefers the GlobalID.

**Species comes from the `ROWTREE_TYPE` domain, which is English only.**  The
stored codes (`ASH-G`, `HLOC`, `MAPLE-F`) map through the domain to
inverted common names (`ASH - GREEN`, `HONEYLOCUST`, `MAPLE - FREEMAN`), and
`shared.species.english.species_from_common_name` resolves those -- 134 of
the 156 codes the standing trees use, 66,995 of 72,350 rows.  `GENUS` and
`FAMILY` are scientific but blank on 40% of active rows and misspelt in the
domain (`Gleditsa`, `Crateagus`, `Koelreutaria`), so they are not used.
The other 21 tree names in use (5,145 rows; 3,367 of them `ELM - HYBRID`,
the city's Accolade/Triumph/Frontier elms, which resolve to the genus because
they are interspecific hybrids), and 11 domain names no standing tree uses
yet, are in the shared table's Naperville section.  The
remaining 210 rows (146 `UNKNOWN`, 64 blank) publish as `Unknown`.

`CULTIVAR` (filled on 15% of active rows: `Autumn Blaze` 6,087, `Bradford`
1,310, `Accolade` 653) is mapped to `cultivar`; values written entirely in
capitals (`AUTUMN BLAZE`, `TRIUMPH`) are title-cased so one selection is one
value.

**DBH is `DIAMETER`, inches**, as measured at the last verification visit:
mode 2 (new plantings), maximum 62, 307 null and 99 zero (nulled by the
schema).  `EST_DBH` is not a measurement -- it is `DIAMETER` grown forward
by a per-species model (median +4.8 in) -- and is not used.

**`DatePlanted` is `plant_date`**, filled on 18,890 active rows from 2004 on.
Six carry three-digit years (`0202`, `0204`, `0205`) and one is 2029-04-29;
the schema nulls all seven.  After the coordinate check (one row has no
geometry) 72,349 trees are published.  No single date is a stamped default (the largest, 639 on
2021-05-01, is a spring planting batch), so none is nulled here.
"""

import sys
from datetime import date
from functools import cache
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    coded_value_domain,
    esri_ms_to_date,
    esri_point,
    iter_features,
)
from shared.species.english import species_from_common_name
from shared.ingest import (
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services1.arcgis.com/rXJ6QApc2sOtl1Pd/arcgis/rest/services/"
    "ROW_Tree_Inventory/FeatureServer/0"
)

OUT_FIELDS = "OBJECTID,GlobalID,STATUS,ROWTREE_TYPE,CULTIVAR,DIAMETER,DatePlanted"

# `STATUS` domain names that are a standing tree.
STANDING_STATUSES = frozenset({"Active", "Private-Active"})

# `ROWTREE_TYPE` codes that are a place, not a tree.
NOT_A_TREE_TYPES = frozenset({"SITE", "STUMP", "NO SITE", "ANY"})

@cache
def domains() -> tuple[frozenset[int], dict[str, str]]:
    """The standing `STATUS` codes and the `ROWTREE_TYPE` code -> name map."""
    statuses = coded_value_domain(LAYER, "STATUS")
    standing = frozenset(int(code) for code, name in statuses.items() if name in STANDING_STATUSES)
    if not standing:
        raise RuntimeError(
            f"Naperville STATUS domain has no {sorted(STANDING_STATUSES)}: {statuses}"
        )
    return standing, coded_value_domain(LAYER, "ROWTREE_TYPE")


def species_for(code) -> str | None:
    if code is None or not str(code).strip():
        return None
    name = domains()[1].get(str(code).strip())
    if name is None:
        raise RuntimeError(f"Naperville ROWTREE_TYPE {code!r} is not in its domain")
    name = " ".join(name.split())
    return species_from_common_name(name)


def clean_cultivar(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    if text.isupper() and not any(ch.isdigit() for ch in text):
        text = text.title()
    return text


def is_standing_tree(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    if attrs.get("STATUS") not in domains()[0]:
        return False
    return (attrs.get("ROWTREE_TYPE") or "").strip() not in NOT_A_TREE_TYPES


def iter_row_chunks():
    """One ArcGIS page (2,000 features) at a time, geometry in WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        raw_id = str(rec.get("GlobalID") or "").strip().strip("{}").lower()
        if not raw_id:
            print(
                f"Naperville: skipping OBJECTID {rec.get('OBJECTID')} with no GlobalID",
                file=sys.stderr,
            )
            continue
        lat, lon = esri_point(feature.get("geometry"))

        tree_id.append(f"npv-{raw_id}")
        species.append(species_for(rec.get("ROWTREE_TYPE")))
        cultivar.append(clean_cultivar(rec.get("CULTIVAR")))
        plant_date.append(esri_ms_to_date(rec.get("DatePlanted")))
        latitude.append(lat)
        longitude.append(lon)
        raw_dbh = rec.get("DIAMETER")
        dbh.append(float(raw_dbh) if raw_dbh is not None else None)

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["USNPV"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(
        iter_row_chunks(), transform, keep=is_standing_tree, label="Naperville OpenData"
    )
    table = validate_coordinates(table, city="Naperville", city_code="USNPV")
    table = enforce_tree_schema(
        table, city="Naperville", data_source="NAPERVILLE_OPENDATA"
    )
    emit(table)

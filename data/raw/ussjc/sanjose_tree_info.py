#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""San José, CA's street tree inventory, from the city's own ArcGIS Server.

Source: "Street Tree" (ArcGIS item 7db16e012fe8402db45074cd260c8f4e, owner
`sanjose`, credited to the City of San José; listed on data.sanjoseca.gov as
`street-tree` under Creative Commons Attribution), served as
`OPN/OPN_OpenDataService/MapServer/510` from geo.sanjoseca.gov: the trees
along the city's right-of-way and sidewalks -- not trees on private lots or in
parks.  344,880 rows on 2026-11-16, stored in California State Plane III
(wkid 102643) and reprojected to WGS84 by the server (`outSR=4326`).  The
MapServer is paged directly (2,000 a page, ~173 requests): the Hub's CSV and
GeoJSON downloads are exports of this same layer.

**Ownership.**  San José makes the adjacent property owner responsible for a
street tree (the layer's own description), and `OWNEDBY` says so: 222,997 of
the standing trees are `Private` (all but 74 of them `MAINTBY` = General
Fund, i.e. a sidewalk tree the owner looks after), 34,891 are `San Jose`
(medians, backups and special maintenance districts the city maintains), 3
`Other` and 10,406 blank.  They are all street trees in the public
right-of-way, the inventory this map publishes, so every ownership is kept.

**Rows that are not standing trees are dropped, on the portal's own columns.**

* `VACANTSITE` = Yes (76,583 rows): an empty planting site.  72,360 + 1,770 of
  them say so in `NAMESCIENTIFIC` too (`Vacant site`/`Vacant Site`), 1,612 are
  `Stump`; the other 841 keep the species of the tree that was removed (one in six
  of them has no DBH, and most were edited in 2024-26).  The flag is
  the city's statement that the site is empty, so all of them go.
* `NAMESCIENTIFIC` = `Stump` with `VACANTSITE` = No (306): the shared
  `is_not_a_tree` rule, applied here so the count is logged.
* `MAINTENANCENEED` = `Plant Tree` (844 not already dropped): a site waiting
  for a tree.  Nearly all of them carry DBH 1 and no install date or condition --
  the species is the one chosen for the planting, not a tree standing there.
* `MAINTENANCENEED` = `Stump Removal` (36 not already dropped): the tree is
  gone and the stump is awaiting grinding.

267,111 trees are published (2026-11-16).

`CONDITION` = Dead (4,041) are trees (the schema's rule: `Dead` stays).

**The id is `FACILITYID`**, the city's asset number (text, `3` .. `561986`):
non-null and distinct on all 344,880 rows on 2026-11-16, and equal to
`INTID` on every row.  There is no `GlobalID`.  It is checked unique again
at run time by `enforce_tree_schema`; a row without one is dropped with a
count.  `DAVEYID` (`MB 20140207121505`, the 2009-2014 contractor survey's
timestamp id) is also distinct, but only the city's number survives a
re-survey.  Ids are `sjc-{FACILITYID}`.

**Species is `NAMESCIENTIFIC`, whose coded-value domain is the scientific
name itself** (578 codes, `code == name` on every one): binomials, genus-only
entries (`Acer`, `Quercus`), `spp` entries (`Ulmus spp`) and quoted
cultivars (`Platanus acerifolia 'Yarwood'`, `Zelkova serrata ‘Village Green’`
with typographic quotes, one with a non-breaking space).  Whitespace is
normalised and the value is passed through; `enforce_tree_schema` moves the
quoted cultivar to `cultivar`, folds `×`/`x`, reads `spp` as the genus and
publishes `Unknown` as the sentinel.  No common-name column is mapped.

**DBH is `TRUNKDIAM`, whole inches** (`Trunk Diameter (DBH) (in)`; median 10,
p99 37 -- inches, not centimetres).  Zeros (2,664) and the few impossible
values over 200 in are nulled by the schema.

**`crown_width_m` is `DIAMETER`, the canopy diameter in feet** (`Canopy
Diameter (ft)`; median 18, p99 60; canopy feet run about twice the DBH inches,
which is what a crown spread does), times 0.3048.  A hundred values above
120 ft are survey slips and the schema nulls anything over 40 m.  `HEIGHT`
(feet) has no canonical column and is not read.

**`INSTALLDATE` is `plant_date`**: 1,932 standing trees, 2020-05-14 to
2026-11-12, almost all DBH 1 -- the city's new plantings, spread over many
dates, so no placeholder stamp to null.  `ORIGINALINVENTORYDATE` is a survey
date (1976-07-04 on 5,634 rows), not a planting, and is not read.
"""

import sys
from collections import Counter
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
    emit,
    enforce_tree_schema,
    is_not_a_tree,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://geo.sanjoseca.gov/server/rest/services/OPN/OPN_OpenDataService/MapServer/510",
    timeout=180,
)

OUT_FIELDS = (
    "OBJECTID,FACILITYID,NAMESCIENTIFIC,TRUNKDIAM,DIAMETER,INSTALLDATE,"
    "VACANTSITE,MAINTENANCENEED"
)

FEET_TO_METRES = 0.3048

# `MAINTENANCENEED` values that mean there is no standing tree at the site.
NO_TREE_NEEDS = {
    "plant tree": "planting site (MAINTENANCENEED = Plant Tree)",
    "stump removal": "stump awaiting removal (MAINTENANCENEED = Stump Removal)",
}

KNOWN_VACANT_FLAGS = frozenset({"Yes", "No", None})


def tree_id_for(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return f"sjc-{text}" if text else None


def clean_species(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def number(value) -> float | None:
    return float(value) if value is not None else None


class SanJose:
    def __init__(self) -> None:
        self.dropped: Counter[str] = Counter()

    def keep(self, feature: dict) -> bool:
        rec = feature.get("attributes") or {}
        vacant = rec.get("VACANTSITE")
        if vacant not in KNOWN_VACANT_FLAGS:
            raise RuntimeError(
                f"San José VACANTSITE {vacant!r} is new; decide whether it is a tree"
            )
        if vacant == "Yes":
            self.dropped["vacant site (VACANTSITE = Yes)"] += 1
            return False
        if is_not_a_tree(clean_species(rec.get("NAMESCIENTIFIC"))):
            self.dropped["stump or vacant site (NAMESCIENTIFIC)"] += 1
            return False
        need = " ".join(str(rec.get("MAINTENANCENEED") or "").split()).lower()
        if need in NO_TREE_NEEDS:
            self.dropped[NO_TREE_NEEDS[need]] += 1
            return False
        if tree_id_for(rec.get("FACILITYID")) is None:
            self.dropped["no FACILITYID"] += 1
            return False
        return True

    def __call__(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        plant_date: list[date | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []
        crown: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            tree_id.append(tree_id_for(rec.get("FACILITYID")))
            species.append(clean_species(rec.get("NAMESCIENTIFIC")))
            plant_date.append(esri_ms_to_date(rec.get("INSTALLDATE")))
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            dbh.append(number(rec.get("TRUNKDIAM")))
            spread_ft = number(rec.get("DIAMETER"))
            crown.append(spread_ft * FEET_TO_METRES if spread_ft is not None else None)

        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["USSJC"] * len(tree_id), type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "plant_date": pa.array(plant_date, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
                "crown_width_m": pa.array(crown, type=pa.float64()),
            }
        )

    def report(self) -> None:
        for reason, n in sorted(self.dropped.items()):
            print(f"San José: dropped {n:,} rows: {reason}", file=sys.stderr)


def iter_row_chunks():
    """One ArcGIS page (2,000 features) at a time, geometry in WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


if __name__ == "__main__":
    san_jose = SanJose()
    table = stream_to_table(
        iter_row_chunks(), san_jose, keep=san_jose.keep, label="San Jose OpenData"
    )
    san_jose.report()
    table = validate_coordinates(table, city="San Jose", city_code="USSJC")
    table = enforce_tree_schema(table, city="San Jose", data_source="SANJOSE_OPENDATA")
    emit(table)

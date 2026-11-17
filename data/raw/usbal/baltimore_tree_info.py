#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Baltimore's street and park tree inventory, from Baltimore City Recreation & Parks Forestry.

Source: `Foresty/TreeInventory_Public/MapServer/0` ("Trees") on the City's
own ArcGIS Server, gis.baltimorecity.gov -- the service description reads
"Baltimore Street Tree Inventory for map service publishing and data
sharing".  It is the layer behind Forestry's "Baltimore Tree Inventory" web
map (ArcGIS item d2cfbbe9a24b4d988de127852e6c26c8, owner
`baltimore_forestry`), 193,007 rows on 2026-11-16, stored in Maryland State
Plane feet (wkid 2893) and reprojected to WGS84 by the server.  The same
feature class is also served as the editing service `Foresty/Trees/
FeatureServer/0` (same row count, same OBJECTIDs); the public layer is read
because it is the one published for sharing, and the extra editing columns
(`CONDITION`, 800 `CULTIVAR`s) are not worth depending on an editing
endpoint.  The freshness probe does read that service's `last_edited_date`,
because the public layer has no date column.

**A third of the layer is planting sites and stumps, and `SPP` says which.**
`SPP` (a coded-value domain whose codes are the scientific names) holds
`Vacant Site` (44,683), `Vacant Potential` (21,541), `Vacant Site Not
Suitable` (3,834), `Stump` (8,998) and `Z Add 01` (227, every one with DBH 0
in a `Potential Well/Pit`, `CONDITION` = Absent in the editing service): the
web map's own "Trees" layer is defined as `SPP NOT IN ('Vacant Site','Vacant
Potential','Stump','Vacant Site Not Suitable')`.  All five are dropped, as is
anything `is_not_a_tree` recognises.  Dead trees (2,601 by `CONDITION`) are
not flagged in the public layer and stay, as the schema keeps dead trees.

**The id is `UniqueID`, Forestry's record id** (collector initials and a
timestamp, `MXB20170419142334`, or initials, date and OBJECTID for records
from Cityworks).  There is no `GlobalID` in the public layer.  It is non-null
and non-blank on all 193,007 rows and repeated on one value
(`ZEG20240808341586`: two zelkovas at 200 and 201 Bolton Pl, distinct trees
given the same id).  Repeated values are read up front with one `groupBy ...
having` statistics query and those rows take `balt-{UniqueID}-{OBJECTID}`,
every other row `balt-{UniqueID}` (Columbus's rule).  `ID` is not the key: it
is null on 273 rows and repeated.  The prefix is `balt-` because Ballarat
(AUBAL) already uses `bal-`.

**Species is `SPP`**, already a scientific name (`Acer rubrum`, `Prunus
spp.`, `Ulmus x`, `Gleditsia triacanthos inermis`); `spp.`, a bare hybrid
mark and infraspecific ranks are cut by the shared schema.  `COMMON` is the
domain's common name and is not mapped.  `SPECIES_OVERRIDES` reads the few
codes that are not a taxon as written: `unknown shrub` (the Shrub sentinel),
`Prunus x incam` ('Okame') and `Ilex x attenuata-Fosteri` ('Fosteri').
`unknown tree` publishes as Unknown.  A null `SPP` with a positive DBH is a
measured, unidentified tree (Unknown); with no DBH it is dropped.

**DBH is `DBH`, inches** (median 8.2 on tree rows, 95th percentile 30, maximum 90.3); zeros are
nulled by the schema.  `TREE_HT` (feet) has no canonical column.  The layer
records no planting date.
"""

import json
import sys
from collections import Counter
from functools import cache
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    SHRUB_SPECIES,
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    is_not_a_tree,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://gis.baltimorecity.gov/egis/rest/services/Foresty/TreeInventory_Public/MapServer/0",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,UniqueID,SPP,DBH"

# `SPP` codes that are a site, not a tree (see the module docstring).
NOT_A_TREE_SPP = frozenset(
    {"vacant site", "vacant potential", "vacant site not suitable", "stump", "z add 01"}
)

# `SPP` codes that are not a taxon as written: (species, cultivar).
SPECIES_OVERRIDES: dict[str, tuple[str | None, str | None]] = {
    "unknown shrub": (SHRUB_SPECIES, None),
    "prunus x incam": ("Prunus", "Okame"),
    "ilex x attenuata-fosteri": ("Ilex x attenuata", "Fosteri"),
}

# Refuse when more than this share of the layer is dropped as a site: the
# 2026 layer is 41.1% sites, and a jump means `SPP` changed meaning.
MAX_SITE_SHARE = 0.55


def spp_key(value) -> str:
    return " ".join(str(value or "").split()).lower()


@cache
def repeated_ids() -> frozenset[str]:
    """The `UniqueID` values the layer assigns to more than one row."""
    payload = get_json_with_retry(
        LAYER.query_url,
        params={
            "where": "UniqueID IS NOT NULL",
            "groupByFieldsForStatistics": "UniqueID",
            "outStatistics": json.dumps(
                [{"statisticType": "count", "onStatisticField": "OBJECTID",
                  "outStatisticFieldName": "n"}]
            ),
            "having": "COUNT(OBJECTID) > 1",
            "f": "json",
        },
        timeout=LAYER.timeout,
    )
    if "features" not in payload or payload.get("exceededTransferLimit"):
        raise RuntimeError(f"Baltimore UniqueID duplicate query was incomplete: {payload!r}"[:500])
    return frozenset(str(f["attributes"]["UniqueID"]).strip() for f in payload["features"])


def has_diameter(dbh) -> bool:
    return dbh is not None and float(dbh) > 0


class Baltimore:
    def __init__(self) -> None:
        self.repeated = repeated_ids()
        self.dropped: Counter[str] = Counter()
        self.seen = 0

    def keep(self, feature: dict) -> bool:
        self.seen += 1
        rec = feature.get("attributes") or {}
        key = spp_key(rec.get("SPP"))
        if key in NOT_A_TREE_SPP or is_not_a_tree(rec.get("SPP")):
            self.dropped["vacant site or stump (SPP)"] += 1
            return False
        if not key and not has_diameter(rec.get("DBH")):
            self.dropped["no species and no diameter"] += 1
            return False
        if not str(rec.get("UniqueID") or "").strip():
            self.dropped["no UniqueID"] += 1
            return False
        return True

    def __call__(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            raw_id = str(rec["UniqueID"]).strip()
            if raw_id in self.repeated:
                tree_id.append(f"balt-{raw_id}-{rec['OBJECTID']}")
            else:
                tree_id.append(f"balt-{raw_id}")
            key = spp_key(rec.get("SPP"))
            if key in SPECIES_OVERRIDES:
                sp, cv = SPECIES_OVERRIDES[key]
            else:
                sp, cv = normalize_species(rec.get("SPP")), None
            species.append(sp)
            cultivar.append(cv)
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            raw_dbh = rec.get("DBH")
            dbh.append(float(raw_dbh) if raw_dbh is not None else None)

        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["USBAL"] * n, type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "plant_date": pa.array([None] * n, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            }
        )

    def report(self) -> None:
        for reason, n in sorted(self.dropped.items()):
            print(f"Baltimore: dropped {n:,} rows: {reason}", file=sys.stderr)
        sites = self.dropped["vacant site or stump (SPP)"]
        if self.seen and sites / self.seen > MAX_SITE_SHARE:
            raise RuntimeError(
                f"Baltimore: {sites:,} of {self.seen:,} rows are sites or stumps; "
                "SPP has changed meaning"
            )


def iter_row_chunks():
    """One ArcGIS page (1,000 features) at a time, geometry in WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


if __name__ == "__main__":
    baltimore = Baltimore()
    table = stream_to_table(
        iter_row_chunks(), baltimore, keep=baltimore.keep, label="Baltimore OpenData"
    )
    baltimore.report()
    table = validate_coordinates(table, city="Baltimore", city_code="USBAL")
    table = enforce_tree_schema(table, city="Baltimore", data_source="BALTIMORE_OPENDATA")
    emit(table)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Santa Monica, CA's public trees, from the City's Public Landscape Division.

Source: `Trees/MapServer/0` ("Trees") on the City of Santa Monica's own
ArcGIS Server, gis.santamonica.gov, registered on ArcGIS Online as item
64f5db2cbb224f6d87387999a300b962 (owner `smgov_agoladmin`, "Public Landscape
Division ... over 33,800 trees in the Santa Monica's Urban Forest").  34,992
rows on 2026-11-19, stored in California State Plane V feet (wkid 2229) and
reprojected to WGS84 by the server.  The same table is served as
`Trees/FeatureServer/0` (query only, same count) and is the resource behind
the city's CKAN portal's "Trees Inventory" dataset
(data.santamonica.gov/dataset/trees-inventory, "Weekly").  The service's other
layer, `Trees/MapServer/1` ("Tree Zones"), is pruning-zone polygons.  The
layer is street (`Parkway`, `Tree Well`, `Median`, ...) and park
(`Parkland`, `Park Tree Well`) trees together.

Licence: Open Data Commons Attribution License (ODC-By) v1.0, stated on the
ArcGIS item ("This dataset is made available under the Open Data Commons
Attribution License: https://opendatacommons.org/licenses/by/1-0/") and as the
CKAN dataset's `license_id` (`odc-by-1-0`).

**The id is `inventoryid`**, the inventory system's tree id (10914717 to
11757000): non-null on every row and unique over the whole layer (a `groupBy
... having COUNT > 1` statistics query returned nothing on 2026-11-19), and,
unlike `objectid`, not reassigned when the service is republished.
`assetnumberexternal` is blank on every row.  The ids are `smo-{inventoryid}`;
`enforce_tree_schema` re-checks the grain on every run.

**Sites, stumps and placeholders are rows, and `botanicalname` says which.**
`Vacant site` (974 rows, `recommended` = `Plant`), `Stump` (12), `Stump - not
accessible` (4), `Unsuitable site` (5), `Asphalted well` (3) and `No Replant`
(1) are dropped.  `Unidentified spp.` (9) and `Other tree` (2) are trees the
surveyor did not name, published as Unknown.  Every text value carries a
leading space, which is stripped.  `isvalid` is `Yes` on every row and
`treecondition` blank; there is no removed/retired flag.  Dead trees
(`recommended` = `PLD Inspection-Dead ...`, 66 rows) keep their species.

**Species is `botanicalname`**, a scientific name with the cultivar quoted
(`Ficus microcarpa 'Nitida'`, `Platanus X hispanica 'Yarwood'`); the shared
schema splits the cultivar off.  `Washingtonia filifera X robusta` (6
rows), a cross the schema would cut to its first parent, publishes as the
genus.  `commonname` is not mapped.

**DBH is `actualdbh`, inches** (median 14, 95th percentile 35 on measured
rows), with `dbh` the surveyor's six-inch class (`0-6` ... `31+`).  The
measurement is 0 on 1,554 rows (745 of them vacant sites) and above 100 on
189 -- 101 and 103 on 137 of those, mostly Canary Island pines and palms,
112 of them in a class that contradicts the value (`0-6` beside 103): a
code, not a 2.6 m trunk.  Both fall back to the class midpoint (Denver's
rule, `DBH_CLASS_INCHES`); `---` is no class and leaves the tree unsized.

**Crown is `canopy_spread__ft_`, feet** (median 20, maximum 99), published as
`crown_width_m`; 0 and null are no measurement.  `height` is a class and
`actualheight` is 0 on nearly every row; neither has a canonical column.
The layer records no planting date.
"""

import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    UNKNOWN_SPECIES,
    emit,
    enforce_tree_schema,
    is_not_a_tree,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://gis.santamonica.gov/server/rest/services/Trees/MapServer/0",
    timeout=180,
)
OUT_FIELDS = "objectid,inventoryid,botanicalname,dbh,actualdbh,canopy_spread__ft_"

ID_PREFIX = "smo-"

# `botanicalname` values that are a site, not a tree (lower-cased).
NOT_A_TREE_NAMES = frozenset(
    {"vacant site", "stump", "stump - not accessible", "unsuitable site",
     "asphalted well", "no replant"}
)

# `botanicalname` values that are a tree nobody named.
UNNAMED_TREE_NAMES = frozenset({"unidentified spp.", "other tree"})

# `botanicalname` values that are not a taxon as written (lower-cased).  The
# fan-palm cross would otherwise be cut to its first parent.
SPECIES_OVERRIDES: dict[str, str] = {
    "washingtonia filifera x robusta": "Washingtonia",
}

# The `dbh` class's midpoint in inches; the open `31+` takes its lower bound
# plus half the common width (see the module docstring).
DBH_CLASS_INCHES: dict[str, float] = {
    "0-6": 3.0,
    "07-12": 9.5,
    "13-18": 15.5,
    "19-24": 21.5,
    "25-30": 27.5,
    "31+": 34.0,
}
# The `dbh` values that mean "not classed".
NO_DBH_CLASS = frozenset({"", "---"})

# A measured `actualdbh` above this is a code, not a trunk.
MAX_MEASURED_DBH_IN = 100.0

FEET_TO_METRES = 0.3048

# Refuse when more than this share of the layer is dropped as a site: 2.9%
# in 2026, and a jump means `botanicalname` changed meaning.
MAX_SITE_SHARE = 0.10


def text(value) -> str:
    return " ".join(str(value or "").split())


class SantaMonica:
    def __init__(self) -> None:
        self.dropped: Counter[str] = Counter()
        self.notes: Counter[str] = Counter()
        self.seen = 0

    def keep(self, feature: dict) -> bool:
        self.seen += 1
        rec = feature.get("attributes") or {}
        name = text(rec.get("botanicalname"))
        if name.lower() in NOT_A_TREE_NAMES or is_not_a_tree(name):
            self.dropped["vacant site, stump or unplantable site (botanicalname)"] += 1
            return False
        if rec.get("inventoryid") is None:
            self.dropped["no inventoryid"] += 1
            return False
        return True

    def diameter(self, rec: dict) -> float | None:
        measured = rec.get("actualdbh")
        if measured is not None and 0 < float(measured) <= MAX_MEASURED_DBH_IN:
            return float(measured)
        cls = text(rec.get("dbh"))
        if cls in NO_DBH_CLASS:
            self.notes["unsized (no measurement, no class)"] += 1
            return None
        if cls not in DBH_CLASS_INCHES:
            raise RuntimeError(f"Santa Monica: unknown dbh class {cls!r}")
        reason = "above 100 in" if measured and float(measured) > 0 else "zero"
        self.notes[f"dbh from class midpoint (measurement {reason})"] += 1
        return DBH_CLASS_INCHES[cls]

    def __call__(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []
        crown: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            raw_id = float(rec["inventoryid"])
            if not raw_id.is_integer():
                raise RuntimeError(f"Santa Monica: non-integral inventoryid {raw_id!r}")
            tree_id.append(f"{ID_PREFIX}{int(raw_id)}")
            name = text(rec.get("botanicalname"))
            if not name or name.lower() in UNNAMED_TREE_NAMES:
                species.append(UNKNOWN_SPECIES)
            elif name.lower() in SPECIES_OVERRIDES:
                species.append(SPECIES_OVERRIDES[name.lower()])
            else:
                # Raw string: enforce_tree_schema splits the quoted cultivar.
                species.append(name)
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            dbh.append(self.diameter(rec))
            spread = rec.get("canopy_spread__ft_")
            crown.append(
                float(spread) * FEET_TO_METRES if spread is not None and float(spread) > 0 else None
            )

        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["USSMO"] * n, type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "plant_date": pa.array([None] * n, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
                "crown_width_m": pa.array(crown, type=pa.float64()),
            }
        )

    def report(self) -> None:
        for reason, n in sorted(self.dropped.items()):
            print(f"Santa Monica: dropped {n:,} rows: {reason}", file=sys.stderr)
        for note, n in sorted(self.notes.items()):
            print(f"Santa Monica: {n:,} trees: {note}", file=sys.stderr)
        sites = sum(self.dropped.values())
        if self.seen and sites / self.seen > MAX_SITE_SHARE:
            raise RuntimeError(
                f"Santa Monica: {sites:,} of {self.seen:,} rows dropped as sites; "
                "botanicalname has changed meaning"
            )


def iter_row_chunks():
    """One ArcGIS page (2,000 features) at a time, geometry in WGS84."""
    return iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326, order_by="objectid"
    )


if __name__ == "__main__":
    santa_monica = SantaMonica()
    table = stream_to_table(
        iter_row_chunks(), santa_monica, keep=santa_monica.keep, label="Santa Monica OpenData"
    )
    santa_monica.report()
    table = validate_coordinates(table, city="Santa Monica", city_code="USSMO")
    table = enforce_tree_schema(
        table, city="Santa Monica", data_source="SANTA_MONICA_OPENDATA"
    )
    emit(table)

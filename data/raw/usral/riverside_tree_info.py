#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Riverside, CA's tree inventory, from the City of Riverside's open data.

Licence: the City of Riverside publishes no named licence.  The ArcGIS items'
licence field is the template placeholder ("My Use Limitations").  The City's
Open Data Policy (Administrative Manual 03.016.00, 20 June 2017) says
datasets are made available "without any registration requirements" and
asks that "proper credit or citation should be given when using or re-using
content, data, documentation, code, and related materials from the City of
Riverside" ("Data provided by the City of Riverside").  It is credited that way.

Source: four hosted layers owned by `rivopendata_admin` on the City's ArcGIS
Online (org Fu2oOWg1Aw7azh41), all exported from the same work-management
system (West Coast Arborists' ArborAccess, which writes `SysID`) and all last
loaded in April 2023:

* `Street_Trees/FeatureServer/0` (item 18ac0db05b7340d1b463d953fdc672fc),
  127,750 rows: the street-tree inventory.
* `Trees_Along_Utility_Lines/FeatureServer/0` (item 4de3951872e8470db65c9faf7142296c),
  49,020 rows: trees Riverside Public Utilities keeps clear of its lines,
  half of them behind a house (`SideOfProperty` = Back).  Same schema.
  The backyard rows (27,067) are private trees and are dropped: the map
  shows public trees.  The rest (front, side, median, slope, park) stay.
* `Miscellaneous_Trees/FeatureServer/0` (item 8b46ef7c6fc7459692f6e94d76766ee1),
  686 rows: the golf course and other city grounds.  Same schema.
* `Park_Trees/FeatureServer/0` (item 0b58f74739fe49b7bb8533f89f326bd3),
  5,718 rows: park trees.  Same attributes but no `SysID`.

`Victoria_Avenue_Trees` (2,879 points) is not read: every attribute but a
`SysID` from a different numbering is null, so it says nothing the street
layer does not.

**Every row is one tree with its own point.**  `TreeCount` reads like a
group size (a row with 171) but is the tree's sequence number along its
address and side: 1508 Gratton St holds trees 1..15 down the Monroe St side,
each with its own `SysID` and a point five metres from the last.  It is not
mapped.

**The id is `SysID`** (`riv-{SysID}`), ArborAccess's record id: non-null on
every row of the three layers that carry it, unique over each layer and
across them (checked at run time: a repeat raises), and sparse (12.3M to
13.6M for 127,750 street rows), so it is a stable record id and not a reload
row number.  The park layer has only a dense `OBJECTID` 1..5,718, so its
trees are keyed on the rounded coordinate (`riv-pk-{lon}_{lat}`,
`coordinate_tree_id`), and rows stacked on one point are dropped
(`drop_stacked_coordinates`).

**Species is `BotanicalName`**, already scientific (`Washingtonia robusta`,
`Platanus X hispanica`, `Fraxinus velutina 'Modesto'`); the shared schema
moves the quoted cultivar and cuts `spp.` and infraspecific ranks.
`CommonName` is not mapped.  Values that are not a tree are dropped:
`Vacant site` (32,637 street rows, `RecommendedTreatment` = Plant), `Stump`,
`Stump - not accessible`, `No tree at this address`, and on the utility
layer, which records whatever grows into the lines, `Pole` (6,988), `No
longer a utility encroachment` (3,000: the plant was cleared and its species
blanked), `Ivy on Pole / Cable`, `Vines`, `Brush`, `Poor planting site` and
the vine and grass genera (ivy, giant reed, bougainvillea, honeysuckle,
bamboo; about 520 rows).  The three shrub values publish as the Shrub
sentinel.  `Dead Tree` is a tree (the Dead sentinel);
`Other tree`, `Not City Planted/Maintained` and `WCA worked on private tree`
are trees the record does not name and publish as Unknown; `Malus spp. & cv.`
is Malus.  `Palm spp.` reaches the Palm sentinel through the shared hygiene.

**DBH** is two columns, both inches: `DBH`, a band chosen on every inspected
row (`0-6`, `07-12`, `13-18`, `19-24`, `25-30`, `31+`; `---` on vacant
sites), and `ActualDBH`, a measured diameter on about a quarter of rows.  The
measurement is used where it agrees with its band (within an inch either
side) or where there is no band; otherwise the band's midpoint (Denver's rule:
the open `31+` band takes its bound plus half the common width, 34), since a
measurement outside the band its own inspector chose is a keying slip (10
typed as 100).  An unknown band label counts toward a 1% refusal.
`Height` is a band in feet with no canonical column.  No planting date is
recorded.
"""

import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    SHRUB_SPECIES,
    UNKNOWN_SPECIES,
    coordinate_tree_id,
    drop_stacked_coordinates,
    emit,
    enforce_tree_schema,
    is_not_a_tree,
    normalize_species,
    validate_coordinates,
)

SERVICES = "https://services.arcgis.com/Fu2oOWg1Aw7azh41/arcgis/rest/services"

# (layer, keyed by SysID).  Street first: it is the inventory, and a SysID
# repeated across layers is reported against the later one.
LAYERS: tuple[tuple[str, bool], ...] = (
    ("Street_Trees", True),
    ("Trees_Along_Utility_Lines", True),
    ("Miscellaneous_Trees", True),
    ("Park_Trees", False),
)
SYSID_FIELDS = "OBJECTID,SysID,BotanicalName,DBH,ActualDBH,SideOfProperty"

# Utility-line trees behind a house are private (see the module docstring).
PRIVATE_SIDE_LAYER = "Trees_Along_Utility_Lines"
PRIVATE_SIDES = frozenset({"back"})
PARK_FIELDS = "OBJECTID,BotanicalName,DBH,ActualDBH"

ID_PREFIX = "riv"
PARK_ID_PREFIX = "riv-pk"

# `BotanicalName` values that are not a tree (dropped) or not a taxon as
# written (read as the species given).  Keys are lowercased and
# whitespace-collapsed.
NOT_A_TREE_NAMES = frozenset(
    {
        "vacant site", "stump", "stump - not accessible", "no tree at this address",
        # The utility layer records what grows into the lines, not only trees.
        "pole", "ivy on pole / cable", "vines", "brush", "poor planting site",
        # A record whose plant was cleared from the lines, species dropped.
        "no longer a utility encroachment",
    }
)
# Genera the utility layer records that are vines or grasses, not trees.
NOT_A_TREE_GENERA = frozenset(
    {"hedera", "arundo", "bougainvillea", "lonicera", "bambusa", "phyllostachys"}
)
SPECIES_OVERRIDES: dict[str, str] = {
    "small shrub under 15'": SHRUB_SPECIES,
    "large shrub 15' or more": SHRUB_SPECIES,
    "shrub/hedge prune": SHRUB_SPECIES,
    "other tree": UNKNOWN_SPECIES,
    "not city planted/maintained": UNKNOWN_SPECIES,
    "wca worked on private tree": UNKNOWN_SPECIES,
    "malus spp. & cv.": "Malus",
}

# DBH bands (inches, inclusive) and their midpoints.  `31+` is open-topped:
# its bound plus half the common six-inch width.
DBH_BANDS: dict[str, tuple[float, float, float]] = {
    "0-6": (0.0, 6.0, 3.0),
    "07-12": (7.0, 12.0, 9.5),
    "13-18": (13.0, 18.0, 15.5),
    "19-24": (19.0, 24.0, 21.5),
    "25-30": (25.0, 30.0, 27.5),
    "31+": (31.0, float("inf"), 34.0),
}
NO_BAND = frozenset({"", "---"})
BAND_SLACK_IN = 1.0
MAX_BAND_PARSE_FAILURES = 0.01

# Refuse when more than this share of the street layer is dropped as a site:
# the 2023 layer is 26.2% vacant sites and stumps.
MAX_SITE_SHARE = 0.40


def name_key(value) -> str:
    return " ".join(str(value or "").split()).lower()


class Riverside:
    def __init__(self) -> None:
        self.dropped: Counter[str] = Counter()
        self.seen: Counter[str] = Counter()
        self.sites: Counter[str] = Counter()
        self.dbh_source: Counter[str] = Counter()
        self.band_failures: Counter[str] = Counter()
        self.sysids: dict[int, str] = {}

    def diameter(self, band_raw, actual_raw) -> float | None:
        band = " ".join(str(band_raw or "").split())
        actual = float(actual_raw) if actual_raw is not None and float(actual_raw) > 0 else None
        if band in NO_BAND:
            self.dbh_source["measured, no band" if actual is not None else "none"] += 1
            return actual
        bounds = DBH_BANDS.get(band)
        if bounds is None:
            self.band_failures[band] += 1
            return actual
        lo, hi, mid = bounds
        if actual is not None and lo - BAND_SLACK_IN <= actual <= hi + BAND_SLACK_IN:
            self.dbh_source["measured"] += 1
            return actual
        self.dbh_source["band midpoint (measurement outside band)" if actual is not None else "band midpoint"] += 1
        return mid

    def rows(self, layer_name: str, keyed: bool) -> pa.Table:
        layer = FeatureLayer(f"{SERVICES}/{layer_name}/FeatureServer/0", timeout=180)
        tree_id: list[str] = []
        species: list[str | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []
        pages = iter_features(
            layer,
            out_fields=SYSID_FIELDS if keyed else PARK_FIELDS,
            return_geometry=True,
            out_sr=4326,
        )
        for page in pages:
            for feature in page:
                self.seen[layer_name] += 1
                rec = feature.get("attributes") or {}
                if layer_name == PRIVATE_SIDE_LAYER and name_key(rec.get("SideOfProperty")) in PRIVATE_SIDES:
                    self.dropped[f"{layer_name}: behind the house (SideOfProperty = Back), private"] += 1
                    continue
                key = name_key(rec.get("BotanicalName"))
                if key in NOT_A_TREE_NAMES or is_not_a_tree(rec.get("BotanicalName")):
                    self.sites[layer_name] += 1
                    self.dropped[f"{layer_name}: not a tree ({key})"] += 1
                    continue
                if key.split(" ")[0] in NOT_A_TREE_GENERA:
                    self.dropped[f"{layer_name}: vine or grass"] += 1
                    continue
                lat, lon = esri_point(feature.get("geometry"))
                if lat is None or lon is None:
                    self.dropped[f"{layer_name}: no geometry"] += 1
                    continue
                if keyed:
                    sysid = rec.get("SysID")
                    if sysid is None:
                        self.dropped[f"{layer_name}: no SysID"] += 1
                        continue
                    sysid = int(sysid)
                    if sysid in self.sysids:
                        raise RuntimeError(
                            f"Riverside SysID {sysid} is repeated "
                            f"({self.sysids[sysid]} and {layer_name}); the id is no longer unique"
                        )
                    self.sysids[sysid] = layer_name
                    tree_id.append(f"{ID_PREFIX}-{sysid}")
                else:
                    tree_id.append(coordinate_tree_id(PARK_ID_PREFIX, lon, lat))
                species.append(SPECIES_OVERRIDES.get(key) or normalize_species(rec.get("BotanicalName")))
                latitude.append(lat)
                longitude.append(lon)
                dbh.append(self.diameter(rec.get("DBH"), rec.get("ActualDBH")))

        n = len(tree_id)
        table = pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["USRAL"] * n, type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "plant_date": pa.array([None] * n, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            }
        )
        if not keyed:
            table = drop_stacked_coordinates(
                table, label=f"Riverside {layer_name}",
                identical=("species", "diameter_at_breast_height"),
            )
        print(f"Riverside {layer_name}: {self.seen[layer_name]:,} rows read, {table.num_rows:,} kept",
              file=sys.stderr)
        return table

    def report(self) -> None:
        for reason, n in sorted(self.dropped.items()):
            print(f"Riverside: dropped {n:,} rows: {reason}", file=sys.stderr)
        for source, n in self.dbh_source.most_common():
            print(f"Riverside DBH: {n:,} rows {source}", file=sys.stderr)
        street = self.seen["Street_Trees"]
        if not street:
            raise RuntimeError("Riverside Street_Trees returned no rows")
        share = self.sites["Street_Trees"] / street
        if share > MAX_SITE_SHARE:
            raise RuntimeError(
                f"Riverside: {share:.1%} of street rows are vacant sites or stumps; "
                "BotanicalName has changed meaning"
            )
        bad = sum(self.band_failures.values())
        read = sum(self.seen.values())
        if bad:
            print(f"Riverside: {bad:,} unknown DBH bands: {self.band_failures.most_common(10)}",
                  file=sys.stderr)
        if read and bad / read > MAX_BAND_PARSE_FAILURES:
            raise RuntimeError(
                f"Riverside: {bad / read:.2%} of rows carry a DBH band this ingest "
                "does not know; the portal has changed its bands"
            )


if __name__ == "__main__":
    riverside = Riverside()
    table = pa.concat_tables([riverside.rows(name, keyed) for name, keyed in LAYERS])
    riverside.report()
    table = validate_coordinates(table, city="Riverside", city_code="USRAL")
    table = enforce_tree_schema(table, city="Riverside", data_source="RIVERSIDE_OPENDATA")
    emit(table)

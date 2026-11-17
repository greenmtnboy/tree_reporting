#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Sacramento, CA's city-maintained trees, from the city's own ArcGIS Online.

Source: "City Maintained Trees" (ArcGIS item b9b716e09b5048179ab648bb4518452b,
owner `Publisher_SacCity`, credited to "Sacramento GIS, Urban Forestry section
of the Department of Public Works"), the hosted layer
`City_Maintained_Trees/FeatureServer/0` behind data.cityofsacramento.org: the
trees and tree sites Urban Forestry maintains in the right-of-way, parks and
other public spaces.  112,746 rows on 2026-11-16, stored in California State
Plane II (wkid 2226) and reprojected by the server (`outSR=4326`).  The item's
`licenseInfo` is only the portal's disclaimer; use is governed by the City of
Sacramento Open Data Terms of Use (in the Open Data Policy-Procedure), which
the portal's other items link to.

No field has a coded-value domain; every column is free text.

**The id is `GISOBJID`**, the city's asset number (3,160,328 .. 100,125,082),
non-null on every row and unique on all but 8 values.  There is no GlobalID;
`ASSET_ID` is the hosted layer's ObjectID, which a republish of the layer
reassigns, and `OBJECTID` is the source geodatabase's (it differs from
`ASSET_ID` on 21k rows, so the layer has been rebuilt at least once).  Seven
of the eight repeated `GISOBJID`s are the same record twice -- same species,
size and point -- and the eighth (`100099738`, a redbud on San Miguel Way)
sits at two points 10 km apart.  The repeated values and their rows are read
up front (a `groupBy ... having` statistics query, then the rows themselves),
so the rule does not depend on page order: a copy at the same point as a
lower-`OBJECTID` row is dropped, and an id still repeated after that keys
every row as `sac-{GISOBJID}-{OBJECTID}`.  Every other row is
`sac-{GISOBJID}`.

**Species is `BOTANICAL`, which is mostly a binomial, and partly not.**

* ~92% of rows carry a binomial, often with a quoted cultivar or a trade name
  (`Ulmus propinqua 'JFS-Bieberich' Emerald Sunshine`) and `spp` for a genus
  identification (`Quercus spp`); `enforce_tree_schema` handles both.  A few
  publisher typos that would otherwise land on a non-taxon or the wrong
  species are fixed in `BOTANICAL_FIXES`.
* ~6% -- park and trail trees entered by a different crew -- carry an
  upper-case common name in `BOTANICAL` (and the same text in `SPECIES`):
  `VALLEY OAK`, `FREMONT COTTONWOOD`, `INTERIOR LIVE OAK`.  These resolve
  first through the layer's own crosswalk -- the inverted common names in
  `SPECIES` that the other rows pair with a binomial (`oak, valley` ->
  *Quercus lobata*), read up front with one statistics query -- then through
  `SACRAMENTO_COMMON_NAMES` (names this layer alone uses), then the shared
  English table.  A name none of them knows publishes as `Unknown`.
* `Vacant site`, `Stump` and `unplantable site` are sites, not trees, and are
  dropped; so is a row with no `BOTANICAL` whose `DESCRIPTION` says
  `vacant`, or which records neither a species, a size nor a description.
  `DEAD TREE` publishes as the `Dead` sentinel, `Unknown spp`/`Other spp` as
  `Unknown`.
* `CULTIVAR` is a free-text note (0.7% of rows), as often a species epithet
  under a genus record (`Quercus spp` + `canyon live`, `Juniperus spp` +
  `rocky moutain`) as a cultivar, and as often noise (`ma`, `NO ROOM`,
  `multi`).  It is read only to refine a genus-only record, through
  `SPP_REFINEMENTS`; it is not published as `cultivar`.  The common-name
  `SPECIES` column is not mapped.

**DBH is `DBH`, a band in inches** (`0 to 3`, `4 to 6`, `7 to 12`, `13 to 18`,
`19 to 24`, `25 to 36`, `37 to 48`, `49+`, plus 636 older rows written
`0 - 6`, `7 - 12`, `13 - 24`, `25 - 36`), read as its midpoint over the
continuous interval (`4 to 6` = (3, 6] -> 4.5; `49+` as 48 plus half the
12-inch width of the bands below it, 54), as West Hollywood's bands are.  An
unseen band counts against a 1% parse-failure guard.

The eight `DBH0TO3` .. `DBH49PLUS` columns are dates (2018-2026), one set per
row on 22k rows: the date the tree was recorded in that size class, i.e. an
inventory date, not a planting date.  No planting date is published, so
`plant_date` is null.  `STEMS`, `LANDUSE`, `PLANTTYPE`, `GROWSPACE`,
`CONDUCTOR` and the address are not canonical columns.

46 trees at Camp Sacramento, the city's camp near Echo Summit 110 km east,
are outside `CITY_BOUNDS` and dropped by `validate_coordinates`.
"""

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from functools import cache
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.species.english import common_name_key, species_from_common_name
from shared.ingest import (
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    is_not_a_tree,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services5.arcgis.com/54falWtcpty3V47Z/arcgis/rest/services/"
    "City_Maintained_Trees/FeatureServer/0",
    timeout=180,
)
OID_FIELD = "ASSET_ID"
OUT_FIELDS = "ASSET_ID,OBJECTID,GISOBJID,BOTANICAL,CULTIVAR,DBH,DESCRIPTION"

# DBH band (inches) -> midpoint of its continuous interval.  Keys are the
# normalised band (`_band_key`).
DBH_BAND_INCHES: dict[str, float] = {
    "0 to 3": 1.5,
    "4 to 6": 4.5,
    "7 to 12": 9.0,
    "13 to 18": 15.0,
    "19 to 24": 21.0,
    "25 to 36": 30.0,
    "37 to 48": 42.0,
    "49+": 54.0,
    # The older crew's bands.
    "0 to 6": 3.0,
    "13 to 24": 18.0,
}
PARSE_FAILURE_LIMIT = 0.01

# `BOTANICAL` values that are sites, not trees, beyond what `is_not_a_tree`
# already recognises (`Vacant site`, `Stump`).
NOT_A_TREE_VALUES = frozenset({"unplantable site"})

# Publisher typos that would publish a non-taxon or the wrong taxon.
# Misspelt epithets that still read as the intended species are left to the
# shared misspelling map.
BOTANICAL_FIXES = {
    # The genus is Ilex.
    "liex aquifolium": "Ilex aquifolium",
    # 'Long' Regal Prince is Q. robur x Q. bicolor; the schema would cut the
    # hybrid formula to its first parent.  The genus is what the row supports.
    "quercus robur x bicolor 'long regal prince'": "Quercus 'Regal Prince'",
    # 'Marina' is a hybrid selection of Arbutus, not a species.
    "arbutus marina": "Arbutus 'Marina'",
    # The Indian laurel fig of the trade is Ficus microcarpa 'Nitida'.
    "ficus nitida": "Ficus microcarpa 'Nitida'",
    # The Afghan (Eldarica) pine; the layer's own `AFGHAN PINE` rows resolve
    # to Pinus brutia through the shared table, so these do too.
    "pinus elderica": "Pinus brutia",
    # A truncated cultivar quote (228 rows).
    "carpinus betulus 'fastigia": "Carpinus betulus 'Fastigiata'",
}

# Upper-case common names in `BOTANICAL` that neither the layer's own
# crosswalk nor the shared English table resolves.  Keys are written plainly
# and normalised with `common_name_key` at import.  A name that is ambiguous
# on its own (`LOCUST`, `RED GUM`, `GREEN PURPLE PLUM`) is left out and
# publishes as Unknown.
_SACRAMENTO_COMMON_NAMES_RAW = {
    "sawtooth zelkova": "Zelkova serrata",
    "village green zelkova": "Zelkova serrata",
    "california sycamore": "Platanus racemosa",
    "brazilian pepper": "Schinus terebinthifolia",
    "shumard red oak": "Quercus shumardii",
    "western redbud": "Cercis occidentalis",
    "so cal black walnut": "Juglans californica",
    "pine lodgepole": "Pinus contorta",
    "oregon ash": "Fraxinus latifolia",
    "narrowleaf willow": "Salix exigua",
    "maple october glory": "Acer rubrum",
    "red sunset maple": "Acer rubrum",
    "maple crimson sentry": "Acer platanoides",
    "maple autumn blaze": "Acer x freemanii",
    "maple truncatum": "Acer truncatum",
    "norway red pine": "Pinus resinosa",
    "purple leaf flowering plum": "Prunus cerasifera",
    "dwarf purple leaf plum": "Prunus x cistena",
    "muskogee crape myrtle": "Lagerstroemia",
    "sour gum": "Nyssa sylvatica",
    "upright hornbeam": "Carpinus betulus",
    "long regal prince oak": "Quercus",
    "hollyleaf cherry": "Prunus ilicifolia",
    "goodings willow": "Salix gooddingii",
    "sweet bay": "Laurus nobilis",
    "marina arbutus": "Arbutus",
    "arroyo willow": "Salix lasiolepis",
    "elderberry": "Sambucus",
    "purple robe locust": "Robinia pseudoacacia",
    "akebono yoshino cherry": "Prunus x yedoensis",
    "pittosporum": "Pittosporum",
    "indian bean": "Catalpa bignonioides",
    "cider gum": "Eucalyptus gunnii",
    "nectarine": "Prunus persica",
    "cape fig": "Ficus sur",
    "monkey puzzle": "Araucaria araucana",
    "date pulm": "Phoenix dactylifera",
    "river she oak": "Casuarina cunninghamiana",
    "blueblossom": "Ceanothus thyrsiflorus",
    "lemon scented gum": "Corymbia citriodora",
    "camperdown elm": "Ulmus glabra",
    "new bradford pear": "Pyrus calleryana",
    "gambel oak": "Quercus gambelii",
    "canyon live oak": "Quercus chrysolepis",
    "black poplar": "Populus nigra",
    "red willow": "Salix laevigata",
}
SACRAMENTO_COMMON_NAMES = {
    common_name_key(k): v for k, v in _SACRAMENTO_COMMON_NAMES_RAW.items()
}

# `CULTIVAR` notes that name the species under a genus-only (`spp`) record.
# Keys are lower-case with `species is ` stripped.
SPP_REFINEMENTS: dict[str, dict[str, str]] = {
    "Quercus": {
        "canyon live": "Quercus chrysolepis",
        "gambel": "Quercus gambelii",
        "gamble oak": "Quercus gambelii",
        "english": "Quercus robur",
        "english oak": "Quercus robur",
        "quercus robur": "Quercus robur",
        "quercus bicolor": "Quercus bicolor",
        "swamp white": "Quercus bicolor",
        "shumard": "Quercus shumardii",
        "shumardii": "Quercus shumardii",
        "shurmand oak": "Quercus shumardii",
        "quercus marilandica": "Quercus marilandica",
        "chinkapin": "Quercus muehlenbergii",
        "chickapin": "Quercus muehlenbergii",
        "accutissima": "Quercus acutissima",
        "sawtooth": "Quercus acutissima",
    },
    "Juniperus": {"rocky moutain": "Juniperus scopulorum"},
    "Acer": {
        "hedge": "Acer campestre",
        "hedge maple": "Acer campestre",
        "acer campestre": "Acer campestre",
        "campestre": "Acer campestre",
        "shantung": "Acer truncatum",
        "shantung maple": "Acer truncatum",
        "sugar": "Acer saccharum",
        "bigleaf maple": "Acer macrophyllum",
    },
    "Prunus": {"apricot": "Prunus armeniaca", "peach": "Prunus persica"},
    "Pinus": {
        "eldarica": "Pinus brutia",
        "pinus eldarica": "Pinus brutia",
        "afghan pine": "Pinus brutia",
        "mugo": "Pinus mugo",
        "scotch": "Pinus sylvestris",
    },
    "Fraxinus": {
        "oregon": "Fraxinus latifolia",
        "american": "Fraxinus americana",
        "americana": "Fraxinus americana",
    },
    "Betula": {"river birch": "Betula nigra"},
    "Cornus": {"pacific": "Cornus nuttallii"},
}

_SPP = re.compile(r"^([A-Z][a-z]+)\s+spp\.?$")


def clean(value) -> str:
    return " ".join(str(value or "").split())


def _band_key(value) -> str:
    text = clean(value).replace(" - ", " to ")
    return " ".join(text.split())


def statistics(group_by: str, *, having: str | None = None) -> list[dict]:
    params = {
        "where": "1=1",
        "groupByFieldsForStatistics": group_by,
        "outStatistics": json.dumps(
            [{"statisticType": "count", "onStatisticField": OID_FIELD,
              "outStatisticFieldName": "n"}]
        ),
        "f": "json",
    }
    if having:
        params["having"] = having
    payload = get_json_with_retry(LAYER.query_url, params=params, timeout=LAYER.timeout)
    if "features" not in payload or payload.get("exceededTransferLimit"):
        raise RuntimeError(f"Sacramento statistics query was incomplete: {payload!r}"[:500])
    return [f["attributes"] for f in payload["features"]]


@cache
def layer_crosswalk() -> dict[str, str]:
    """common_name_key(SPECIES) -> BOTANICAL, from the layer's binomial rows."""
    votes: dict[str, Counter[str]] = defaultdict(Counter)
    for row in statistics("SPECIES,BOTANICAL"):
        common, botanical = clean(row.get("SPECIES")), clean(row.get("BOTANICAL"))
        if not common or not botanical or botanical.upper() == botanical:
            continue
        key = common_name_key(common)
        if key:
            votes[key][botanical] += row["n"]
    # `maple, Norway` pairs with `Acer platanoides` (97) and its 'Crimson
    # Sentry' (4): the plurality is the plain taxon.
    return {key: counts.most_common(1)[0][0] for key, counts in votes.items()}


def resolve_common_name(name: str) -> str | None:
    key = common_name_key(name)
    crosswalk = layer_crosswalk()
    if key in crosswalk:
        return crosswalk[key]
    if key in SACRAMENTO_COMMON_NAMES:
        return SACRAMENTO_COMMON_NAMES[key]
    return species_from_common_name(name)


def species_value(botanical: str, cultivar: str) -> str | None:
    if not botanical:
        return None
    if botanical.upper() == botanical:
        return resolve_common_name(botanical)
    botanical = BOTANICAL_FIXES.get(botanical.lower(), botanical)
    match = _SPP.match(botanical)
    if match and cultivar:
        note = cultivar.lower().removeprefix("species is ").strip()
        refined = SPP_REFINEMENTS.get(match.group(1), {}).get(note)
        if refined:
            return refined
    return botanical


def is_site(botanical: str) -> bool:
    return is_not_a_tree(botanical) or botanical.lower() in NOT_A_TREE_VALUES


def repeated_ids() -> tuple[dict[int, str], set[int]]:
    """(OBJECTID -> tree_id for rows of a repeated GISOBJID, OBJECTIDs to drop)."""
    repeated = [
        int(r["GISOBJID"])
        for r in statistics("GISOBJID", having=f"COUNT({OID_FIELD}) > 1")
        if r.get("GISOBJID") is not None
    ]
    ids: dict[int, str] = {}
    drop: set[int] = set()
    if not repeated:
        return ids, drop
    rows = [
        f
        for page in iter_features(
            LAYER,
            out_fields="OBJECTID,GISOBJID",
            where=f"GISOBJID IN ({','.join(map(str, repeated))})",
            return_geometry=True,
            order_by=OID_FIELD,
        )
        for f in page
    ]
    groups: dict[int, list[dict]] = defaultdict(list)
    for f in rows:
        groups[int(f["attributes"]["GISOBJID"])].append(f)
    for gid, members in groups.items():
        members.sort(key=lambda f: f["attributes"]["OBJECTID"])
        kept: list[dict] = []
        points: set[tuple[float, float]] = set()
        for f in members:
            lat, lon = esri_point(f.get("geometry"))
            point = (round(lat or 0.0, 6), round(lon or 0.0, 6))
            if point in points:
                drop.add(f["attributes"]["OBJECTID"])
            else:
                points.add(point)
                kept.append(f)
        for f in kept:
            oid = f["attributes"]["OBJECTID"]
            ids[oid] = f"sac-{gid}" if len(kept) == 1 else f"sac-{gid}-{oid}"
    return ids, drop


class Sacramento:
    def __init__(self) -> None:
        self.repeated_ids, self.duplicate_oids = repeated_ids()
        self.dropped: Counter[str] = Counter()
        self.band_failures: Counter[str] = Counter()
        self.unresolved: Counter[str] = Counter()
        self.rows = 0

    def keep(self, feature: dict) -> bool:
        rec = feature.get("attributes") or {}
        botanical = clean(rec.get("BOTANICAL"))
        if botanical and is_site(botanical):
            self.dropped[f"site, not a tree ({botanical.lower()})"] += 1
            return False
        if not botanical:
            description = clean(rec.get("DESCRIPTION")).lower()
            if description.startswith("vacant"):
                self.dropped["no BOTANICAL, DESCRIPTION says vacant"] += 1
                return False
            if not description and not clean(rec.get("DBH")):
                self.dropped["no BOTANICAL, DBH or DESCRIPTION"] += 1
                return False
        if rec.get("GISOBJID") is None:
            self.dropped["no GISOBJID"] += 1
            return False
        if rec.get("OBJECTID") in self.duplicate_oids:
            self.dropped["duplicate record of a GISOBJID at the same point"] += 1
            return False
        return True

    def __call__(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            oid = rec.get("OBJECTID")
            tree_id.append(self.repeated_ids.get(oid) or f"sac-{int(rec['GISOBJID'])}")

            botanical = clean(rec.get("BOTANICAL"))
            value = species_value(botanical, clean(rec.get("CULTIVAR")))
            if botanical and value is None:
                self.unresolved[botanical] += 1
            species.append(value)

            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)

            band = _band_key(rec.get("DBH"))
            if not band:
                dbh.append(None)
            elif band in DBH_BAND_INCHES:
                dbh.append(DBH_BAND_INCHES[band])
            else:
                self.band_failures[band] += 1
                dbh.append(None)
        self.rows += len(tree_id)

        n = len(tree_id)
        plant_date: list[date | None] = [None] * n
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["USSAC"] * n, type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "plant_date": pa.array(plant_date, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            }
        )

    def report(self) -> None:
        for reason, n in sorted(self.dropped.items()):
            print(f"Sacramento: dropped {n:,} rows: {reason}", file=sys.stderr)
        if self.unresolved:
            print(
                f"Sacramento: {sum(self.unresolved.values()):,} rows carry a common name "
                f"nothing resolves (published as Unknown): "
                f"{dict(self.unresolved.most_common())}",
                file=sys.stderr,
            )
        failures = sum(self.band_failures.values())
        if failures:
            print(
                f"Sacramento: {failures:,} unreadable DBH bands: "
                f"{dict(self.band_failures.most_common(10))}",
                file=sys.stderr,
            )
        if self.rows and failures / self.rows > PARSE_FAILURE_LIMIT:
            raise RuntimeError(
                f"Sacramento: {failures:,} of {self.rows:,} DBH bands are unreadable; "
                "the band labels have changed"
            )


def iter_row_chunks():
    """One page (2,000 features) at a time, geometry in WGS84."""
    return iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326, order_by=OID_FIELD
    )


if __name__ == "__main__":
    sacramento = Sacramento()
    table = stream_to_table(
        iter_row_chunks(), sacramento, keep=sacramento.keep, label="Sacramento OpenData"
    )
    sacramento.report()
    table = validate_coordinates(table, city="Sacramento", city_code="USSAC")
    table = enforce_tree_schema(
        table, city="Sacramento", data_source="SACRAMENTO_OPENDATA"
    )
    emit(table)

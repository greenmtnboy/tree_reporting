#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Fingal County Council's tree inventory ("Trees_FCC").

Source: the hosted layer `Trees_FCC/FeatureServer/0` (item
1e5f9db62e53443d946c15a1a06fd98b, owner `FingalAdmin`), listed on the
council's open data hub (data.fingal.ie) and harvested to data.gov.ie as
`trees-fcc2`.  The item's credits read "Contains Irish Public Sector Data
licensed under a Creative Commons Attribution 4.0 International (CC BY 4.0)
licence", and data.gov.ie lists it as CC-BY-4.0.  35,978 rows on 2026-11-16;
the layer was last edited 2023-02-01 and is the same extract (by `Tree_ID`
and point) as the 2021 KML on data.smartdublin.ie, so it changes rarely.

**Locations are approximate, by the council's own disclaimer**: some points
sit at the dwelling the tree stands outside rather than at the tree.
Measured against OpenStreetMap on 2026-11-16: 18% of trees fall inside a
building footprint (72% in Carpenterstown, 55% in Blanchardstown, 42% in
Castleknock, under 6% outside Dublin 15) and 8.7% share their exact point
with another tree (1,026 stacks, up to 40 trees on one point).  A point in a
house is typically 5-15 m from the verge the tree stands on.  Published as
recorded: the cross-source dedup and the position policy are where it is
corrected, not here.

**`Tree_ID` is the key** (`fingal-26388`): the council's integer asset id,
never null, 35,976 distinct values over 35,978 rows.  There is no GlobalID
and `ObjectId` is renumbered by a republish.  Of the two repeats, `26388` is
one tree recorded twice 5 m apart with identical attributes (the first by
`ObjectId` is kept); one of the two `182200` rows is an empty record,
dropped below.  Repeats that name different trees are dropped whole, with a
count.

**`Species_Desc` is the scientific name** (`Acer platanoides 'Crimson
King'`); `Tree_Species` is the council's code for it (`ACPLC`) and
`Common_Name` the English name, which is not published.  The code says what
is not a tree: `AAAA` (`Suitable location`, 661, a planting site) and `STMP`
(`Stump`, 143) are dropped by code.  `DEAD` (50) is the `Dead` sentinel;
`G. Shrubbery` and `Hedgerow Hawthorn >50%` are `Shrub`; `Unknown
Broadleaf`/`Unknown Conifer` and the two `G Group` values (a group of trees
under one point, kept as one tree as the inventory counts it) are `Unknown`.
`SOURCE_SPELLINGS` rewrites the freehand cultivars (`Prunus serrulata
Kanzan`, `Sorbus Joseph Rock`, `Ulmus lobeli`) and the abbreviated
`Crat. oxyacanthoides 'Paul's Scarlet'`, whose apostrophe breaks the shared
quote rule, to a binomial and a cultivar column.  A tree with `Condition`
Dead but a species keeps its species.

489 rows carry no species code at all; the 183 of them that also carry no
age, height, spread, condition or trunk are empty records and are dropped
with a count; the other 306 publish as `Unknown`.

**`Actual_Trunk` is the trunk diameter in centimetres** (`17cm`), not a
girth: newly planted trees have a median of 4 cm and trees over 20 m a
median of 90 cm, which as circumferences would be 1.3 cm and 29 cm trunks.
`0cm` (2,302) is unmeasured; zeros and the few impossible values (1,752 cm)
are nulled by enforce_tree_schema.  `Trunk` is the same value banded and is
not read.  More than `MAX_PARSE_FAILURES` unreadable values refuses the
build.  **`Spread` is the crown spread in metres, banded** (`Up to 3
metres` ... `Over 12 metres`), published as the band's midpoint in
`crown_width_m`.  `Height` is a band with no canonical column, and
`Age_Desc` a life stage, not a planting date; there is no planting date.
"""

import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    DEAD_SPECIES,
    SHRUB_SPECIES,
    emit,
    enforce_tree_schema,
    validate_coordinates,
)
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features

LAYER = FeatureLayer(
    "https://services5.arcgis.com/CI1e5PKQXvJgmJK8/arcgis/rest/services/"
    "Trees_FCC/FeatureServer/0",
    timeout=180,
)
OUT_FIELDS = (
    "ObjectId,Tree_ID,Tree_Species,Species_Desc,Actual_Trunk,Spread,"
    "Age_Desc,Height,Condition"
)
ID_PREFIX = "fingal-"

# Codes for rows that are not trees.
NOT_A_TREE_CODES = frozenset({"AAAA", "STMP"})  # suitable location, stump
DEAD_CODES = frozenset({"DEAD"})
SHRUB_CODES = frozenset({"SHRUB", "HEDGE"})
# A tree nobody identified, or a group recorded under one point.
UNKNOWN_CODES = frozenset({"UNB", "UNC", "GPSU", "GPS"})

# Freehand names, rewritten to (species, cultivar).  Keyed on the source value
# with its whitespace collapsed.
SOURCE_SPELLINGS: dict[str, tuple[str, str | None]] = {
    "Prunus serrulata Kanzan": ("Prunus serrulata", "Kanzan"),
    "Sorbus Joseph Rock": ("Sorbus", "Joseph Rock"),
    "Ulmus lobeli": ("Ulmus", "Lobel"),
    "Cedrus atlantica Glauca": ("Cedrus atlantica", "Glauca"),
    "Quercus Fastigiata": ("Quercus robur", "Fastigiata"),
    "Carpinus fastigiata 'Frans Fontaine'": ("Carpinus betulus", "Frans Fontaine"),
    "Crat. oxyacanthoides 'Paul's Scarlet'": ("Crataegus oxyacanthoides", "Paul's Scarlet"),
    # Abies numidica x homolepis: a hybrid with no binomial; the genus is
    # what can be said.
    "Abis numidica x homolepsis": ("Abies", None),
}

# Crown-spread bands, metres -> midpoint.  `Over 12 metres` is open-topped.
CROWN_BAND_M: dict[str, float] = {
    "Up to 3 metres": 1.5,
    "3 to 6 metres": 4.5,
    "6 to 9 metres": 7.5,
    "9 to 12 metres": 10.5,
    "Over 12 metres": 13.5,
}

TRUNK_CM = re.compile(r"(\d+(?:\.\d+)?)\s*cm", re.IGNORECASE)
MAX_PARSE_FAILURES = 0.01

# Columns whose presence says a row with no species is still a recorded tree.
DESCRIPTIVE_FIELDS = ("Age_Desc", "Height", "Spread", "Condition")

# Two rows under one Tree_ID are one tree when they sit within this distance.
SAME_TREE_METRES = 15.0


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


class Reader:
    """Counts the values one column's parser could not read."""

    def __init__(self, name: str):
        self.name = name
        self.read = 0
        self.failures: Counter[str] = Counter()

    def fail(self, text: str) -> None:
        self.failures[text] += 1

    def check(self) -> None:
        bad = sum(self.failures.values())
        if not bad:
            return
        share = bad / max(self.read, 1)
        print(
            f"Fingal ingest: {bad} unreadable `{self.name}` values ({share:.2%}): "
            f"{self.failures.most_common(10)}",
            file=sys.stderr,
        )
        if share > MAX_PARSE_FAILURES:
            raise RuntimeError(
                f"Fingal `{self.name}`: {share:.2%} of rows unreadable; the council "
                "has changed the column's format"
            )


def trunk_cm(value, reader: Reader) -> float | None:
    reader.read += 1
    text = clean(value)
    if text is None:
        return None
    match = TRUNK_CM.fullmatch(text)
    if match is None:
        reader.fail(text)
        return None
    return float(match.group(1))


def crown_m(value, reader: Reader) -> float | None:
    reader.read += 1
    text = clean(value)
    if text is None:
        return None
    mid = CROWN_BAND_M.get(text)
    if mid is None:
        reader.fail(text)
    return mid


def species_of(attrs: dict) -> tuple[str | None, str | None]:
    """(species, cultivar); a None species publishes as `Unknown`."""
    code = clean(attrs.get("Tree_Species"))
    code = code.upper() if code else None
    if code in DEAD_CODES:
        return DEAD_SPECIES, None
    if code in SHRUB_CODES:
        return SHRUB_SPECIES, None
    if code in UNKNOWN_CODES:
        return None, None
    text = clean(attrs.get("Species_Desc"))
    if text is None:
        return None, None
    if text in SOURCE_SPELLINGS:
        return SOURCE_SPELLINGS[text]
    return text, None


def is_blank_record(attrs: dict) -> bool:
    """No species and nothing else that says a tree was surveyed there."""
    if clean(attrs.get("Tree_Species")) or clean(attrs.get("Species_Desc")):
        return False
    if any(clean(attrs.get(f)) for f in DESCRIPTIVE_FIELDS):
        return False
    trunk = clean(attrs.get("Actual_Trunk"))
    return trunk is None or trunk.lower() == "0cm"


def metres_apart(a: tuple, b: tuple) -> float:
    (lat1, lon1), (lat2, lon2) = a, b
    dy = (lat2 - lat1) * 111_320
    dx = (lon2 - lon1) * 111_320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def one_record(group: list[dict]) -> dict | None:
    """One tree recorded under one id more than once, or None when they disagree."""
    first = group[0]
    points = [esri_point(f.get("geometry")) for f in group]
    if any(None in p for p in points):
        return None
    if any(metres_apart(points[0], p) > SAME_TREE_METRES for p in points[1:]):
        return None
    attrs = [{k: v for k, v in f["attributes"].items() if k != "ObjectId"} for f in group]
    if any(a != attrs[0] for a in attrs[1:]):
        return None
    return first


def fetch() -> list[dict]:
    by_id: dict[int, list[dict]] = defaultdict(list)
    total = no_id = not_a_tree = blank = 0
    for page in iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, order_by="ObjectId"
    ):
        for feature in page:
            total += 1
            attrs = feature.get("attributes") or {}
            if attrs.get("Tree_ID") is None:
                no_id += 1
                continue
            code = clean(attrs.get("Tree_Species"))
            if code and code.upper() in NOT_A_TREE_CODES:
                not_a_tree += 1
                continue
            if is_blank_record(attrs):
                blank += 1
                continue
            by_id[int(attrs["Tree_ID"])].append(feature)

    records: list[dict] = []
    merged = conflicting = 0
    for key, group in by_id.items():
        if len(group) == 1:
            record = group[0]
        else:
            record = one_record(sorted(group, key=lambda f: f["attributes"]["ObjectId"]))
            if record is None:
                conflicting += len(group)
                continue
            merged += len(group) - 1
        record["key"] = key
        records.append(record)
    print(
        f"Fingal OpenData: {total} rows; dropped {no_id} with no Tree_ID, "
        f"{not_a_tree} planting sites and stumps, {blank} empty records with no "
        f"species or survey data, {merged} repeated records of one tree, and "
        f"{conflicting} rows whose repeated Tree_ID names different trees; "
        f"{len(records)} trees",
        file=sys.stderr,
    )
    return records


def transform(records: list[dict]) -> pa.Table:
    trunk_reader = Reader("Actual_Trunk")
    crown_reader = Reader("Spread")

    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []

    for record in records:
        attrs = record["attributes"]
        lat, lon = esri_point(record.get("geometry"))
        name, selection = species_of(attrs)
        cm = trunk_cm(attrs.get("Actual_Trunk"), trunk_reader)
        tree_id.append(f"{ID_PREFIX}{record['key']}")
        species.append(name)
        cultivar.append(selection)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(cm / 2.54 if cm is not None else None)
        crown.append(crown_m(attrs.get("Spread"), crown_reader))

    trunk_reader.check()
    crown_reader.check()

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["IEFIN"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array([None] * len(tree_id), type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            "crown_width_m": pa.array(crown, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = transform(fetch())
    table = validate_coordinates(table, city="Fingal", city_code="IEFIN")
    table = enforce_tree_schema(table, city="Fingal", data_source="FINGAL_OPENDATA")
    emit(table)

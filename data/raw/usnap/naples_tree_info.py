#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Naples, FL's tree inventory.

Source: "City Trees" (`Community_Services/CityTrees/FeatureServer/0`) on the
City of Naples' own ArcGIS Server, g.naplesgov.com.  The service describes it
as the city's urban forest "compiled into a single dataset ... collected with
high accuracy GPS equipment"; the city's tree viewer draws city-owned trees in
red and private trees in green.  29,535 rows on 2026-11-11, of which 29,366
are trees.  The layer is stored in Florida State Plane East (feet) and
reprojected to WGS84 by the server (`outSR=4326`).  Paging and the Esri epoch
live in `shared.platforms.arcgis`.

**`Category` decides whether a row is a tree.**  Its coded-value domain is
`Palm` (13,725), `Hardwood` (5,890), `Understory` (962), `Private` (8,789)
and `Vacant` (166).  `Private` is not a kind of tree but the trees standing
on private lots that the city surveyed alongside its own (`Parkway` reads
`Private` on all but three of them); they carry a species, a diameter and a
height like any other row, so they stay.  `Vacant` is an empty planting site
(its `Scientific_Name` is `Vacant` and its DBH 0 or null) and is dropped.
Three rows have no category at all: two have no geometry, and the third
(OBJECTID 41901) is a point created in 2024 with every attribute empty; they
are unfinished records and are dropped.  Any other category raises, because a
new code is a schema change and guessing its side of the line would publish
the wrong trees.  The domain also offers nothing like `Stump` or `Removed`; a
`Stump` species code exists in the `Scientific_Name` domain but no row uses it,
and the shared hygiene drops it if one ever does.

**Species is `Scientific_Name`**, a coded-value domain whose codes are the
names themselves except one: the code `Sabal Pamletto` (57 rows) is displayed
`Sabal Palmetto`, so the ingest reads the domain and maps code to display
name.  The names carry cultivars in quotes (`Bucida buceras 'Shady Lady'`,
`Ilex x attenuata 'East Palatka'`), which the shared schema moves to
`cultivar`.  The domain's own typos (`Querus Virginaina`, `Sereona repens`,
`Cananaga odorata`, `Terminalia muellerii`, ...) are folded centrally by
`SPECIES_MISSPELLINGS`, not here.  `Common_Name` is the common name, through
`normalize_tree_name`.

**`DBH` is inches**, measured on palms as on any other tree: the palm median
is 12 in and the hardwood median 15 in.  It is filled on every tree row but
twenty; the 120 zeros are almost all `Vacant` sites (dropped) and the shared
schema nulls the three on trees.  The layer's `Height` column is total height
in feet; the canonical schema has no height column (`height_m` comes from the
LIDAR crown lookup, docs/TREE_HEIGHT.md), so it is not emitted.

**`plant_date` is `Planting_Year`**, a free-text year filled on 2,426 rows,
all plantings of 2018-2023 (the city's replanting programme).  Ten read
`2020 = E` (an estimated 2020); the leading four-digit year is taken and
dated 1 January.  `<Null>` and blanks are missing.  A value that is none of
these counts as a parse failure, and more than 1% of them refuses the build.

**The id is `OBJECTID`**, the runbook's last resort: the layer has no
GlobalID column (`globalIdField` is empty) and no asset number of its own.
It is unique and non-null over all 29,535 rows, and the layer is a versioned
enterprise geodatabase edited in place (OBJECTIDs run to 41,901 with the
deletions' gaps left open), so an id survives an edit.
"""

import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    coded_value_domain,
    esri_point,
    iter_features,
)
from shared.ingest import (
    emit,
    enforce_tree_schema,
    normalize_tree_name,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://g.naplesgov.com/arcgis/rest/services/"
    "Community_Services/CityTrees/FeatureServer/0"
)

OUT_FIELDS = "OBJECTID,Common_Name,Scientific_Name,DBH,Planting_Year,Category"

TREE_CATEGORIES = frozenset({"Palm", "Hardwood", "Understory", "Private"})
NOT_A_TREE_CATEGORIES = frozenset({"Vacant", ""})

MISSING_YEAR = frozenset({"", "<NULL>"})
LEADING_YEAR = re.compile(r"^(\d{4})\b")
MAX_YEAR_PARSE_FAILURES = 0.01


def is_a_tree(category: str | None) -> bool:
    """True for a tree; raises on a category the ingest has not seen."""
    value = (category or "").strip()
    if value in TREE_CATEGORIES:
        return True
    if value in NOT_A_TREE_CATEGORIES:
        return False
    raise RuntimeError(
        f"Naples Category {value!r} is new; decide whether it is a tree"
    )


def clean_text(value: str | None) -> str | None:
    """Collapse whitespace; blank is missing."""
    text = " ".join((value or "").split())
    return text or None


class YearParser:
    """`Planting_Year` to a date, counting the strings it could not read."""

    def __init__(self) -> None:
        self.read = 0
        self.failures: list[str] = []

    def __call__(self, value: str | None) -> date | None:
        text = (value or "").strip()
        if text.upper() in MISSING_YEAR:
            return None
        self.read += 1
        match = LEADING_YEAR.match(text)
        if not match:
            self.failures.append(text)
            return None
        return date(int(match.group(1)), 1, 1)


SPECIES_NAMES: dict[str, str] = {}
YEARS = YearParser()
DROPPED: dict[str, int] = {}


def iter_row_chunks():
    SPECIES_NAMES.update(coded_value_domain(LAYER, "Scientific_Name"))
    return iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    )


def species_of(code: str | None) -> str | None:
    code = clean_text(code)
    if code is None:
        return None
    return clean_text(SPECIES_NAMES.get(code, code))


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
        category = rec.get("Category")
        if not is_a_tree(category):
            key = (category or "").strip() or "<no category>"
            DROPPED[key] = DROPPED.get(key, 0) + 1
            continue
        oid = rec.get("OBJECTID")
        tree_id.append(f"nap-{oid}" if oid is not None else None)
        species.append(species_of(rec.get("Scientific_Name")))
        tree_name.append(normalize_tree_name(clean_text(rec.get("Common_Name"))))
        plant_date.append(YEARS(rec.get("Planting_Year")))
        lat, lon = esri_point(feature.get("geometry"))
        latitude.append(lat)
        longitude.append(lon)
        raw_dbh = rec.get("DBH")
        dbh.append(float(raw_dbh) if raw_dbh is not None else None)

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["USNAP"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "tree_name": pa.array(tree_name, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


def check_year_parse() -> None:
    if YEARS.read and len(YEARS.failures) / YEARS.read > MAX_YEAR_PARSE_FAILURES:
        raise RuntimeError(
            f"Naples Planting_Year: {len(YEARS.failures)} of {YEARS.read} values "
            f"unreadable (e.g. {YEARS.failures[:5]!r})"
        )
    if YEARS.failures:
        print(
            f"Naples: {len(YEARS.failures)} unreadable Planting_Year values nulled",
            file=sys.stderr,
        )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Naples OpenData")
    print(f"Naples: dropped non-tree rows {DROPPED}", file=sys.stderr)
    check_year_parse()
    table = validate_coordinates(table, city="Naples, FL", city_code="USNAP")
    table = enforce_tree_schema(
        table, city="Naples, FL", data_source="NAPLES_OPENDATA"
    )
    emit(table)

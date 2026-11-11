#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Houston, TX's street-tree inventory.

Source: the "Trees" layer (`03_BaseData_External/Land_Base/FeatureServer/0`)
on the City's ArcGIS Server at geohwp.houstontx.gov, published by Houston Parks
& Recreation / Greenspace Management / Urban Forestry.  The layer describes
itself as "a partial inventory of City of Houston street-trees ... Last updated
May 2019"; the publishing script stamps the layer description "This layer was
last updated: 2022-07-22", which is what the freshness probe reads.  215,139
rows on 2026-11-11, every one a point; nothing is dropped as a non-tree,
because the layer has no site-status column and records no empty sites (the
shared rule still drops any species value that names one).  Paging and the
reprojection to WGS84 live in `shared.platforms.arcgis`.

The layer has eight columns: `OBJECTID`, `SPECIES`, `CAN_DIAM`, the address
(`ADDRESS`, `ST_NAME`, `ADDRESSNUM`) and `LATITUDE`/`LONGITUDE` attributes.
Position comes from the geometry, reprojected server-side from State Plane
Texas South Central; the attribute pair agrees with it to about a metre.

**`OBJECTID` is the key, as the last resort it is.**  The layer has no
GlobalID (`globalIdField` is empty) and no asset number of its own: 215,139
distinct OBJECTIDs, no nulls, 1..215,139 dense.  The ids are only as stable
as the publisher's republishing -- a truncate-and-append renumbers them -- and
there is nothing better to key on: never the address, which several trees
share, and never a positional hash.

**`SPECIES` is an English common name, not a binomial**, mostly inverted for
sorting (`Oak, Live`, `Hackberry, Common`).  The field has no coded-value
domain, so there is no publisher mapping to read; the name is resolved through
`shared.species.english`.  Before the lookup the ingest strips two things the
column carries that are not part of a name -- a numeric code prefix on 121
rows (`02 - Crape Myrtle`, `04 - Oak, Water`) and a `, spp.` suffix
(`Palm, spp.`, which then resolves to the `Palm` sentinel) -- and corrects the
handful of the layer's own misspellings and 20-character truncations in
`SOURCE_SPELLINGS` (`Magnolia, Souther`, `Cedar, Northern Whit`).

**`Cedar` is `Juniperus` here, not the table's `Thuja occidentalis`.**  The
shared table's `cedar` is the eastern white cedar of Ontario and New
Brunswick and its docstring says a city outside that range must check before
reusing it.  On the Texas Gulf Coast an unqualified "cedar" is a juniper
(eastern red cedar, Ashe juniper); the genus is what the source recorded, so
the 1,038 bare `Cedar` rows publish as `Juniperus`.  `Cedar, Eastern Red` and
`Cedar, Deodar` carry their own names and go through the table as written.

Names the table does not hold publish as `Unknown`, by the shared module's
rule that an unresolved common name is never guessed; the run prints them on
stderr with their counts.  When the city was wired that was 25,615 rows in
124 names, led by `Chinese Tallow` (9,561), `Arizona Ash` (3,793) and
`Bradford Pear` (2,380), which wait on entries in `COMMON_NAME_SPECIES`; with
`Unknown`, `Other` and blanks the city published 27,367 `Unknown` rows of
215,139.

**`CAN_DIAM` is read as a DBH class in inches, despite its alias.**  The
layer aliases it "Canopy Diameter", but it is ten five-unit classes (`00 to
4.9` ... `40 to 44.9`, `45+`) and the per-species distributions only make
sense as trunk diameter: 80% of the 44,000 crape myrtles, 91% of yaupons and
83% of redbuds sit in the lowest class (a crown under five feet across is a
nursery whip; a stem under five inches is what those trees are), and only 308
of 44,059 live oaks reach the top class (a live oak crown over 45 feet is
ordinary in Houston; a 45-inch trunk is rare).  So it maps to
`diameter_at_breast_height` and not to `crown_width_m`.  Each class is read as
its midpoint, and the open-topped `45+` as its bound plus half the class
width, Denver's rule; 121 rows carry the bare lower bound (`10`, `05`), read
as that class.  `PVT` (391 rows, a private tree the crew did not measure) and
blanks are null.  Any other value counts as unreadable, and more than 1% of
rows unreadable refuses the build.

The layer records no planting date, no cultivar column and no status, so
`plant_date` is an all-null `date32`.  `Pear, Bradford` is the one name that
carries a selection, so it supplies cultivar `Bradford` (which
`enforce_tree_schema` keeps only once the name resolves to a taxon).  `tree_name` is the
cleaned source name through `normalize_tree_name`, null for `Unknown`,
`Other` and the one `Deadwooding` row (a work order, not a tree).
"""

import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.species.english import common_name_key, species_from_common_name
from shared.ingest import (
    emit,
    enforce_tree_schema,
    normalize_tree_name,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://geohwp.houstontx.gov/arcgis/rest/services/"
    "03_BaseData_External/Land_Base/FeatureServer/0"
)

OUT_FIELDS = "OBJECTID,SPECIES,CAN_DIAM"

# `02 - Crape Myrtle`: an old species code left in front of the name.
CODE_PREFIX = re.compile(r"^\d+\s*-\s*")
# `Palm, spp.`: a genus written as a rank, which the lookup cannot invert.
SPP_SUFFIX = re.compile(r",\s*spp?\.?$", re.IGNORECASE)

# The layer's own misspellings and truncations, corrected before lookup and
# display.  Keys are the source value after the prefix/suffix strip.
SOURCE_SPELLINGS: dict[str, str] = {
    "Magnolia, Souther": "Magnolia, Southern",
    "Cedar, Northern Whit": "Cedar, Northern White",
    "Hackberry, Lindheime": "Hackberry, Lindheimer",
    "Oak, Nuttal": "Oak, Nuttall",
    "Elm, CHinese": "Elm, Chinese",
    "Avacado": "Avocado",
    "Bauhania": "Bauhinia",
    "Jathropa": "Jatropha",
    "Jujuba": "Jujube",
    "Harthorn, Parsley": "Hawthorn, Parsley",
    "Pine, Monterrey": "Pine, Monterey",
}

# Regional readings that override or extend the shared table (see the
# docstring), keyed by `common_name_key`.  Each is right on the Gulf Coast and
# wrong somewhere else, which is why they live here: cherry laurel is the
# Carolina laurel cherry (Prunus laurocerasus in Europe); "Japanese plum" is
# the loquat (Prunus salicina elsewhere); "orchid" is the orchid tree; mountain
# laurel is the Texas mountain laurel (Kalmia in the north-east); black pine is
# the Japanese black pine of the coastal trade (Pinus nigra in Europe).
REGIONAL_SPECIES: dict[str, str] = {
    "cedar": "Juniperus",
    "cherry laurel": "Prunus caroliniana",
    "japanese plum": "Eriobotrya japonica",
    "orchid": "Bauhinia",
    "mountain laurel": "Dermatophyllum secundiflorum",
    "black pine": "Pinus thunbergii",
}

# Values that name no tree; they publish as `Unknown` with no tree_name.
NOT_A_NAME = frozenset({"", "unknown", "other", "deadwooding"})

CULTIVARS: dict[str, str] = {
    "Pear, Bradford": "Bradford",
}

# DBH classes, inches -> midpoint.  `45+` is open-topped: bound plus half the
# five-inch width.  The bare lower bound is the same class written short.
DBH_CLASS_INCHES: dict[str, float] = {
    "00 to 4.9": 2.5,
    "05 to 9.9": 7.5,
    "10 to 14.9": 12.5,
    "15 to 19.9": 17.5,
    "20 to 24.9": 22.5,
    "25 to 29.9": 27.5,
    "30 to 34.9": 32.5,
    "35 to 39.9": 37.5,
    "40 to 44.9": 42.5,
    "45+": 47.5,
}
DBH_CLASS_INCHES.update(
    {label.split(" ")[0].rstrip("+"): mid for label, mid in DBH_CLASS_INCHES.items()}
)
NOT_MEASURED = frozenset({"", "PVT"})
MAX_DBH_PARSE_FAILURES = 0.01

DBH_READ = 0
DBH_FAILURES: list[str] = []
UNRESOLVED: Counter[str] = Counter()


def clean_name(value: str | None) -> str:
    """The source name with its code prefix, `spp.` suffix and typos removed."""
    text = " ".join((value or "").split())
    text = CODE_PREFIX.sub("", text)
    text = SPP_SUFFIX.sub("", text).strip()
    return SOURCE_SPELLINGS.get(text, text)


def species_of(name: str) -> str | None:
    """A cleaned common name as a `species` value; None publishes `Unknown`."""
    if name.lower() in NOT_A_NAME:
        return None
    regional = REGIONAL_SPECIES.get(common_name_key(name))
    if regional is not None:
        return regional
    resolved = species_from_common_name(name)
    if resolved is None:
        UNRESOLVED[name] += 1
    return resolved


def parse_dbh(value: str | None) -> float | None:
    """A `CAN_DIAM` class to its midpoint in inches, counting unreadable ones."""
    global DBH_READ
    DBH_READ += 1
    text = (value or "").strip()
    if text in NOT_MEASURED:
        return None
    inches = DBH_CLASS_INCHES.get(text)
    if inches is None:
        DBH_FAILURES.append(text)
    return inches


def iter_row_chunks():
    return iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    )


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    tree_name: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        oid = rec.get("OBJECTID")
        tree_id.append(f"hou-{oid}" if oid is not None else None)
        name = clean_name(rec.get("SPECIES"))
        species.append(species_of(name))
        cultivar.append(CULTIVARS.get(name))
        tree_name.append(
            None if name.lower() in NOT_A_NAME else normalize_tree_name(name)
        )
        lat, lon = esri_point(feature.get("geometry"))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(parse_dbh(rec.get("CAN_DIAM")))

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["USHOU"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "tree_name": pa.array(tree_name, type=pa.string()),
            "plant_date": pa.array([None] * n, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


def check_dbh_parse() -> None:
    if DBH_READ and len(DBH_FAILURES) / DBH_READ > MAX_DBH_PARSE_FAILURES:
        raise RuntimeError(
            f"Houston CAN_DIAM: {len(DBH_FAILURES)} of {DBH_READ} values "
            f"unreadable (e.g. {sorted(set(DBH_FAILURES))[:5]!r})"
        )
    if DBH_FAILURES:
        print(
            f"Houston: {len(DBH_FAILURES)} unreadable CAN_DIAM values nulled "
            f"({Counter(DBH_FAILURES).most_common(5)})",
            file=sys.stderr,
        )


def report_unresolved() -> None:
    if UNRESOLVED:
        top = ", ".join(f"{name!r} {n}" for name, n in UNRESOLVED.most_common(15))
        print(
            f"Houston: {sum(UNRESOLVED.values())} rows in {len(UNRESOLVED)} common "
            f"names not in COMMON_NAME_SPECIES publish as Unknown: {top}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Houston OpenData")
    check_dbh_parse()
    report_unresolved()
    table = validate_coordinates(table, city="Houston, TX", city_code="USHOU")
    table = enforce_tree_schema(
        table, city="Houston, TX", data_source="HOUSTON_OPENDATA"
    )
    emit(table)

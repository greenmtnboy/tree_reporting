#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Columbus, OH's public tree inventory, from the city's own ArcGIS Server.

Source: "Public Owned Trees" (ArcGIS item 674e4a358e8042f69a734f229a93823c,
owner `ColumbusOhioGIS`, credited to "City of Columbus, Recreation and Parks
Department, Urban Forestry", licence CC0 as stated in the item's
`licenseInfo` and on opendata.columbus.gov), served as
`Schemas/RecreationParks/MapServer/1` from maps2.columbus.gov: the trees on
streets, parks and public grounds that Urban Forestry plants, inspects, prunes
and removes.  316,808 rows on 2026-11-16, stored in Ohio State Plane South
(wkid 6551) and reprojected to WGS84 by the server (`outSR=4326`).  The
MapServer is paged directly (2,000 a page, ~160 requests): the Hub's CSV and
GeoJSON downloads are exports of this same layer, cached and regenerated on
the Hub's schedule, so they would only add a second freshness question.

**Most of the layer is history or empty sites, and three columns say which.**

* `RETIRED` (a date) is set on 56,053 rows: trees removed and their stumps
  ground (55,665 of them carry `MAINTENANCE` = Stump Grinding).  Dropped.
* `SP_CODE` records the site as well as the tree: `VSS`/`VSM`/`VSL`
  (`Vacant Site, Small/Medium/Large`, 85,912 unretired rows) and `STUMP` are
  places, not trees.  The domain is read at run time and every code whose
  name `is_not_a_tree` recognises is dropped here.
* A removed tree whose stump is still in the ground is not retired yet: the
  city sets `STATUS` = 3 (no domain is published; all 1,888 unretired rows
  were edited in 2025-26, 1,726 of them with `MAINTENANCE` = Removal and
  `NEXT_MAINTENANCE` = Stump Grinding) and/or queues `NEXT_MAINTENANCE` =
  Stump Grinding (another 2,543), and 71 rows already carry `MAINTENANCE` =
  Stump Grinding.  A stump-grinding job means the tree is gone, so all of
  these are dropped.  `MAINTENANCE` = Removal *without* a stump-grinding job
  (2,791 rows, 96% in Poor condition) is an inspector's recommendation, a
  standing tree, and is kept.  Any `STATUS` other than null or 3 raises: the
  value has no domain, and guessing which side of the line it falls on would
  publish the wrong trees.

`SITE_CLASS` is not a vacancy flag (1 = street right-of-way, 2 = park, and
a handful of smaller classes, all holding trees) and is not read.

**The id is `TREE_ID`, made unique where the city repeated it.**  There is no
`GlobalID`.  `TREE_ID` is Urban Forestry's asset number (text, often
zero-padded: `028317`), unique on 316,751 of 316,808 rows.  It is null on 25
rows (dropped, with a count) and repeated on 15 values -- `2677` on 9 trees
planted in a row on S Hamilton Rd, `m` on 4 new plantings on Winshire Dr --
distinct trees a few metres apart that were given the address number or a
placeholder, not duplicated records.  Those rows take `col-{TREE_ID}-{OBJECTID}`
and every other row `col-{TREE_ID}`; the repeated values are read up front
with one `groupBy ... having` statistics query over the whole layer, so the
rule does not depend on which page a row arrives in.  `OBJECTID` alone was the
alternative (max 317,230 over 316,808 rows, so the table has never been
reloaded) but `TREE_ID` is the number the city's own work orders carry.

**Species comes from the `SP_CODE` domain, which is English only.**  The codes
are USDA-style symbols with the city's digit suffixes (`ACRU`, `ACSA2`,
`PI1AB` for Picea, `PI2ST` for Pinus); the domain names them as inverted
common names (`Maple, Red`, `Pine, White, Eastern`, `Arborvitae, Eastern
(White Cedar)`), and `shared.species.english.species_from_common_name`
resolves them.  `columbus_readings` turns a name into the readings the table
can match -- the comma parts reversed (`Eastern White Pine`), then a
parenthesised alias in place of the qualifier (`English Maple` for `Maple,
Hedge (English)`) or on its own (`Tupelo`).  An alias reading is accepted only
when its genus starts with the code's first two letters (`THPL`, western
arborvitae, must not become *Juniperus virginiana* through "red cedar").  A
`spp.` code (`TI`, `Basswood (Linden) spp.`) is a genus identification, so
the answer is cut to its genus.  `2TB`/`2TE` (`Unknown dead hardwood/conifer`)
publish as the `Dead` sentinel and `2TREE` (`Unlnown or other live tree`) as
`Unknown`.  A code missing from the domain (3 in use, 6 rows) publishes as
`Unknown`; more than 1% of rows doing so raises, because then the domain has
been replaced.

`CULTIVAR` (filled on 44k unretired rows: `Ivory Silk`, `Pacific Sunset`,
`Frontier`, `Redmond`) is the `cultivar`, title-cased where written in capitals.

**DBH is `DIAM_BREAST_HEIGHT`, whole inches**: mode 2 (new plantings), median
around 8, a long tail to the 50s; zeros are nulled by the schema.  `HEIGHT`
is null on almost every row and is not a canonical column.

**`PLANTING_DATE` is `plant_date`**, on 57,928 unretired rows from 1994 to
2026.  The largest single dates are 2012-12-30 (1,818) and 2011-12-31 (810):
year-end stamps on young trees of many species (DBH 4-8 in now, i.e. planted
around then), so a year-precision record of a real planting programme, not a
placeholder default; nothing is nulled here.
"""

import json
import re
import sys
from collections import Counter
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
    DEAD_SPECIES,
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    is_not_a_tree,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://maps2.columbus.gov/arcgis/rest/services/Schemas/RecreationParks/MapServer/1",
    timeout=180,
)

OUT_FIELDS = (
    "OBJECTID,TREE_ID,SP_CODE,CULTIVAR,DIAM_BREAST_HEIGHT,PLANTING_DATE,"
    "RETIRED,STATUS,MAINTENANCE,NEXT_MAINTENANCE"
)

# `STATUS` has no published domain.  Null is a standing tree; 3 is a removed
# tree whose stump has not been ground yet (see the module docstring).
STANDING_STATUS = None
STUMP_STATUS = 3
KNOWN_STATUSES = frozenset({STANDING_STATUS, STUMP_STATUS})

# The `MAINTENANCE` / `NEXT_MAINTENANCE` domain name that means the tree is gone.
STUMP_GRINDING = "Stump Grinding"

# `SP_CODE` names that are a dead tree of unknown species.
DEAD_NAMES = frozenset({"unknown dead hardwood", "unknown dead conifer"})

# Codes whose domain name is a species but whose trees are hybrid selections
# of no one species, read from the `CULTIVAR`s Columbus records under them.
# `ULCA` "Elm, Smoothleaf" (U. minor) holds 'Triumph' 951, 'Homestead' 83,
# 'Accolade' 29, 'Pioneer' 24 -- the Asian/European hybrid elms; `PRXF`
# "Cherry, Taiwan" (P. campanulata) holds 'Okame' 846 and 'First Lady' 179,
# both P. campanulata x incisa crosses.  The genus is what the rows support.
CODE_OVERRIDES = {"ULCA": "Ulmus", "PRXF": "Prunus"}

MAX_UNKNOWN_CODE_SHARE = 0.01

_PAREN = re.compile(r"\s*\(([^)]*)\)")
_SPP = re.compile(r"\bspp?\b\.?", re.IGNORECASE)


@cache
def species_codes() -> dict[str, str]:
    return coded_value_domain(LAYER, "SP_CODE")


@cache
def stump_grinding_codes() -> frozenset[str]:
    """The `MAINTENANCE` codes named Stump Grinding (read, not assumed)."""
    codes = frozenset(
        code
        for field in ("MAINTENANCE", "NEXT_MAINTENANCE")
        for code, name in coded_value_domain(LAYER, field).items()
        if name.strip().lower() == STUMP_GRINDING.lower()
    )
    if not codes:
        raise RuntimeError(f"Columbus MAINTENANCE domain has no {STUMP_GRINDING!r}")
    return codes


@cache
def not_a_tree_codes() -> frozenset[str]:
    codes = frozenset(code for code, name in species_codes().items() if is_not_a_tree(name))
    if not codes:
        raise RuntimeError("Columbus SP_CODE domain has no vacant-site or stump code")
    return codes


def columbus_readings(name: str) -> list[tuple[str, bool]]:
    """(reading, is_alias) pairs for one domain name, most literal first.

    `Pine, White, Eastern`             -> Eastern White Pine
    `Maple, Hedge (English)`           -> Hedge Maple; English Maple*; English*
    `Basswood (Linden), White`         -> White Basswood; White Linden*; Linden*
    `Black Gum (Tupelo)`               -> Black Gum; Black Tupelo*; Tupelo*
    `Juniper, Eastern Red Cedar`       -> Eastern Red Cedar Juniper; Eastern Red Cedar*

    `*` marks an alias reading, which must pass the genus check.
    """
    def invert(text: str) -> str:
        parts = [p.strip() for p in text.split(",") if p.strip()]
        return " ".join(reversed(parts))

    alias_match = _PAREN.search(name)
    plain = " ".join(_PAREN.sub("", name).split())
    readings = [(invert(plain), False)]
    if alias_match:
        alias = alias_match.group(1).strip()
        # The alias in place of the word it qualifies: `Hedge (English)` -> `English`.
        head, _, _ = name[: alias_match.start()].rpartition(" ")
        tail = name[alias_match.end():]
        readings.append((invert(f"{head} {alias}{tail}"), True))
        readings.append((alias, True))
    # A qualifier that is itself a whole name: `Juniper, Eastern Red Cedar`.
    parts = [p.strip() for p in plain.split(",") if p.strip()]
    if len(parts) == 2:
        readings.append((parts[1], True))
    seen: set[str] = set()
    out = []
    for text, is_alias in readings:
        text = " ".join(text.split())
        if text and text not in seen:
            seen.add(text)
            out.append((text, is_alias))
    return out


def genus_matches(code: str, species: str) -> bool:
    return species[:2].lower() == code[:2].lower()


@cache
def species_for_code(code: str) -> str | None:
    """`SP_CODE` -> the `species` value, or None for Unknown."""
    if code in CODE_OVERRIDES:
        return CODE_OVERRIDES[code]
    name = species_codes().get(code)
    if name is None:
        return None
    key = " ".join(name.lower().split())
    if key in DEAD_NAMES:
        return DEAD_SPECIES
    genus_only = bool(_SPP.search(name))
    name = " ".join(_SPP.sub("", name).split())
    for reading, is_alias in columbus_readings(name):
        resolved = species_from_common_name(reading)
        if resolved is None:
            continue
        if is_alias and not genus_matches(code, resolved):
            continue
        if genus_only:
            resolved = resolved.split()[0]
        return resolved
    return None


def clean_cultivar(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip("'\"‘’ ")
    if not text:
        return None
    if text.isupper() and not any(ch.isdigit() for ch in text):
        text = text.title()
    return text


def repeated_tree_ids() -> frozenset[str]:
    """The `TREE_ID` values the layer assigns to more than one row."""
    payload = get_json_with_retry(
        LAYER.query_url,
        params={
            "where": "TREE_ID IS NOT NULL",
            "groupByFieldsForStatistics": "TREE_ID",
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
        raise RuntimeError(f"Columbus TREE_ID duplicate query was incomplete: {payload!r}"[:500])
    return frozenset(str(f["attributes"]["TREE_ID"]).strip() for f in payload["features"])


class Columbus:
    def __init__(self) -> None:
        self.repeated = repeated_tree_ids()
        self.dropped: Counter[str] = Counter()
        self.unknown_codes: Counter[str] = Counter()
        self.rows = 0

    def keep(self, feature: dict) -> bool:
        rec = feature.get("attributes") or {}
        status = rec.get("STATUS")
        if status not in KNOWN_STATUSES:
            raise RuntimeError(
                f"Columbus STATUS {status!r} is new; decide whether it is a tree"
            )
        if rec.get("RETIRED") is not None:
            self.dropped["retired"] += 1
            return False
        if (rec.get("SP_CODE") or "").strip() in not_a_tree_codes():
            self.dropped["vacant site or stump (SP_CODE)"] += 1
            return False
        grind = stump_grinding_codes()
        if (
            status == STUMP_STATUS
            or str(rec.get("MAINTENANCE")) in grind
            or str(rec.get("NEXT_MAINTENANCE")) in grind
        ):
            self.dropped["removed, stump awaiting grinding"] += 1
            return False
        raw_id = str(rec.get("TREE_ID") or "").strip()
        if not raw_id:
            self.dropped["no TREE_ID"] += 1
            return False
        return True

    def __call__(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        plant_date: list[date | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            raw_id = str(rec["TREE_ID"]).strip()
            if raw_id in self.repeated:
                tree_id.append(f"col-{raw_id}-{rec['OBJECTID']}")
            else:
                tree_id.append(f"col-{raw_id}")

            code = (rec.get("SP_CODE") or "").strip()
            if code and code not in species_codes():
                self.unknown_codes[code] += 1
            species.append(species_for_code(code) if code else None)
            cultivar.append(clean_cultivar(rec.get("CULTIVAR")))
            plant_date.append(esri_ms_to_date(rec.get("PLANTING_DATE")))
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            raw_dbh = rec.get("DIAM_BREAST_HEIGHT")
            dbh.append(float(raw_dbh) if raw_dbh is not None else None)
        self.rows += len(tree_id)

        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["USCOL"] * len(tree_id), type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "plant_date": pa.array(plant_date, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            }
        )

    def report(self) -> None:
        for reason, n in sorted(self.dropped.items()):
            print(f"Columbus: dropped {n:,} rows: {reason}", file=sys.stderr)
        unknown = sum(self.unknown_codes.values())
        if unknown:
            print(
                f"Columbus: {unknown:,} rows carry an SP_CODE missing from its domain "
                f"(published as Unknown): {dict(self.unknown_codes.most_common())}",
                file=sys.stderr,
            )
        if self.rows and unknown / self.rows > MAX_UNKNOWN_CODE_SHARE:
            raise RuntimeError(
                f"Columbus: {unknown:,} of {self.rows:,} rows have an SP_CODE outside "
                "its domain; the domain has changed"
            )


def iter_row_chunks():
    """One ArcGIS page (2,000 features) at a time, geometry in WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


if __name__ == "__main__":
    columbus = Columbus()
    table = stream_to_table(
        iter_row_chunks(), columbus, keep=columbus.keep, label="Columbus OpenData"
    )
    columbus.report()
    table = validate_coordinates(table, city="Columbus", city_code="USCOL")
    table = enforce_tree_schema(table, city="Columbus", data_source="COLUMBUS_OPENDATA")
    emit(table)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy", "tzdata"]
# ///
"""Coral Springs, Florida's City-maintained trees and palms, from the City's ArcGIS Online organisation.

LICENCE: no licence stated; displayed with attribution only, not licensed for
reuse.  The item's `licenseInfo`, `accessInformation` and description state
no terms (https://www.arcgis.com/home/item.html?id=5a5fd0d701e04a75ba945bf94e71db9f),
the organisation has no open-data site, and the City's website Terms and
Conditions (https://www.coralsprings.gov/Government/City-Attorney/Website-Legal-Notices/Terms-and-Conditions)
are vendor procurement terms that say nothing about published data.

Source: "Cityworks Horticulture Tree Test view Public" (ArcGIS item
5a5fd0d701e04a75ba945bf94e71db9f, owner `plockwitz@coralsprings.gov_COSP` of
the City's own organisation `mXWZ2wsX9klsJAJY`, "City of Coral Springs";
snippet "City horticulture division tree asset inventory for use in
Cityworks test environment.  This is the designated external service for
the Public with editing disabled"), layer `.../FeatureServer/0`: 23,158
rows on 2026-11-27.  It is the only tree layer the City publishes.  Despite
the name it is the live inventory: every row is owned and maintained by the
City (`ownedby = maintby = 10`), surveyed by the Parks & Recreation and
Public Works horticulture crews from September 2023 (`cscreationdate`), with
inspections into 2026 and condition dates to November 2026.  The rows were
loaded into this hosted layer on 2026-04-21 (`CreationDate`), which is also
when every `GlobalID` was minted.  Geometry is stored in Florida East State
Plane feet (EPSG 2881) and read in WGS84 (`outSR=4326`).

**The id is `assetid`** (`coralsprings-{assetid}`, e.g. `PWTRE009219`), the
City's Cityworks asset id: non-null and unique over the whole layer (23,158
distinct values on 2026-11-27).  It is preferred to `GlobalID` because the
GlobalIDs date from the 2026-04-21 load into this *test* view and would be
re-minted when the City publishes its production layer, while the Cityworks
asset id is the record's own and predates the load.  `OBJECTID` (1..23,433,
with gaps) is a row number.  A row with no `assetid` is dropped with a
logged count; a repeat raises (the schema's grain check).

**Which rows are trees.**  `lifecyclestatus` (coded): 8 `In Service` on
23,145 rows; `Removed` (128), `Retired` (64), `History` (48), `Abandoned`
(32) and `Out of Service` (256) are gone and are dropped, as are the
not-yet-planted `Proposed`/`Approved`/`Under Construction` codes; `Unknown`
(0) and the `To Be Retired` codes are still standing and stay.  A code
outside the domain raises.  `condition = Dead` trees are still standing and
stay.

**Test records.**  The view carries the GIS administrator's test edits made
after the load: rows whose `notes` say "test" (`Pl test`, `Test pl`, `Ok
test`), and rows the layer owner created after the load day (2026-04-22
onwards: `66/77/88`, `1000000000` in the size fields).  Both are dropped
with a count.  Field crews' later additions (other creators) stay.

**Species is `namescientific`**, a coded-value domain whose codes are the
botanical names (`Quercus virginiana`, `Conocarpus erectus var. sericeus`,
`Ilex x attenuata`), read through the shared sanitiser, which truncates
varieties to species rank and folds the synonyms and misspellings
(`Quercus viginiana`, `Ligustrum japanicum`).  `Unknown` and `Other` are
`Unknown`.  Epithets written capitalised (`Ficus Elastica`, `Tecoma Stans`)
are lower-cased here.  The domain's one common name, `Carambola`, is the
starfruit (`Averrhoa carambola`).  A value that does not read as a Latin
name counts as unreadable; above 1% the build is refused.  `namecommon` is
not mapped.

**DBH is `diameter`, inches** (median 11 over trees and palms, 99th
percentile 35); 0 and the test row's 10^9 are nulled by the schema.
**`spread` is crown spread in feet** (median 16, 99th percentile 60),
converted to `crown_width_m`; zero, negative and implausible values are
nulled by the schema.  `height` (feet) is recorded but the tree schema has
no recorded-height column, so it is not read.

**`installdate` is the planting date** (20 rows; most are test rows), read
as the local date in America/New_York.
"""

import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_datetime, esri_point, iter_features  # noqa: E402
from shared.ingest import (  # noqa: E402
    UNKNOWN_SPECIES,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

SOURCE_URL = (
    "https://services1.arcgis.com/mXWZ2wsX9klsJAJY/arcgis/rest/services/"
    "Cityworks_Horticulture_Tree_Test_view_Public/FeatureServer/0"
)
LAYER = FeatureLayer(SOURCE_URL, timeout=180)
OUT_FIELDS = (
    "OBJECTID,assetid,lifecyclestatus,namescientific,diameter,spread,"
    "installdate,notes,Creator,CreationDate"
)
ID_PREFIX = "coralsprings"

EASTERN = ZoneInfo("America/New_York")
FEET_TO_M = 0.3048

# `lifecyclestatus` codes (the layer's coded-value domain).
STANDING = frozenset({0, 8, 12, 14, 15, 16, 24})  # unknown, in service, to be retired
GONE = frozenset({32, 48, 64, 128, 256})  # abandoned, history, retired, removed, out of service
NOT_PLANTED = frozenset({1, 2, 3, 4, 6, 7})  # proposed, approved, under construction

# The GIS administrator (the layer's owner) loaded the inventory on this day;
# rows the owner created afterwards are test edits.
LAYER_OWNER = "plockwitz@coralsprings.gov_COSP"
BULK_LOAD_DAY = date(2026, 4, 21)
TEST_NOTE = re.compile(r"\btest\b", re.IGNORECASE)

UNKNOWN_VALUES = frozenset({"unknown", "other"})
# Domain values that are not a botanical name: lower-cased -> binomial.
NAME_FIXES = {"carambola": "Averrhoa carambola"}

MAX_UNPARSED_SHARE = 0.01

# Optional hybrid mark, genus, optional hybrid mark, then lower-case words
# (epithet, `var.`, variety, `sp`, `spp`).
LATIN = re.compile(r"^(X )?[A-Z][a-z]+( x)?( [a-z][a-z.-]*)*$")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("\xa0", " ").split())
    return text or None


class Counts:
    no_id = 0
    no_geometry = 0
    dropped: Counter = Counter()
    unparsed: Counter = Counter()
    named = 0


COUNTS = Counts()


def local_date(value) -> date | None:
    stamp = esri_ms_to_datetime(value)
    return stamp.astimezone(EASTERN).date() if stamp else None


def is_test_record(attrs: dict) -> bool:
    if TEST_NOTE.search(clean(attrs.get("notes")) or ""):
        return True
    created = local_date(attrs.get("CreationDate"))
    return attrs.get("Creator") == LAYER_OWNER and created is not None and created > BULK_LOAD_DAY


def keep(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    status = attrs.get("lifecyclestatus")
    if status in GONE or status in NOT_PLANTED:
        COUNTS.dropped[f"lifecyclestatus {status}"] += 1
        return False
    if status not in STANDING:
        raise RuntimeError(
            f"Coral Springs ingest: lifecyclestatus {status!r} is outside the "
            "domain this ingest has read; decide whether it is a standing tree"
        )
    if is_test_record(attrs):
        COUNTS.dropped["test record"] += 1
        return False
    if not clean(attrs.get("assetid")):
        COUNTS.no_id += 1
        return False
    lat, lon = esri_point(feature.get("geometry"))
    if lat is None or lon is None:
        COUNTS.no_geometry += 1
        return False
    return True


def parse_species(raw) -> str | None:
    """The botanical name as published, for the shared sanitiser.

    Examples:
        "Quercus virginiana"  -> "Quercus virginiana"
        "Ficus Elastica"      -> "Ficus elastica"
        "Other"               -> "Unknown"
        "Carambola"           -> "Averrhoa carambola"
        None                  -> None
    """
    text = clean(raw)
    if text is None:
        return None
    COUNTS.named += 1
    lowered = text.lower()
    if lowered in UNKNOWN_VALUES:
        return UNKNOWN_SPECIES
    if lowered in NAME_FIXES:
        return NAME_FIXES[lowered]
    genus, *rest = text.split(" ")
    text = " ".join([genus, *(token.lower() for token in rest)])
    if not LATIN.match(text):
        COUNTS.unparsed[text] += 1
        return None
    return text


def iter_chunks():
    yield from iter_features(
        LAYER,
        out_fields=OUT_FIELDS,
        return_geometry=True,
        out_sr=4326,
        order_by="OBJECTID",
    )


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    planted: list[date | None] = []
    latitude: list[float] = []
    longitude: list[float] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        tree_id.append(f"{ID_PREFIX}-{clean(attrs['assetid'])}")
        species.append(parse_species(attrs.get("namescientific")))
        planted.append(local_date(attrs.get("installdate")))
        latitude.append(lat)
        longitude.append(lon)
        diameter = attrs.get("diameter")
        dbh.append(float(diameter) if diameter is not None else None)
        spread = attrs.get("spread")
        crown.append(float(spread) * FEET_TO_M if spread is not None else None)

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["USCRS"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "plant_date": pa.array(planted, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        "crown_width_m": pa.array(crown, type=pa.float64()),
    })


if __name__ == "__main__":
    table = stream_to_table(iter_chunks(), transform, keep=keep, label="Coral Springs OpenData")
    dropped = ", ".join(f"{n:,} {why}" for why, n in COUNTS.dropped.most_common())
    print(
        f"Coral Springs ingest: dropped {sum(COUNTS.dropped.values()):,} rows "
        f"({dropped}), {COUNTS.no_id} with no assetid, "
        f"{COUNTS.no_geometry} with no geometry",
        file=sys.stderr,
    )
    unparsed = sum(COUNTS.unparsed.values())
    if unparsed:
        print(
            f"Coral Springs ingest: {unparsed} species values unreadable: "
            f"{COUNTS.unparsed.most_common(10)}",
            file=sys.stderr,
        )
    if COUNTS.named and unparsed / COUNTS.named > MAX_UNPARSED_SHARE:
        raise RuntimeError(
            f"Coral Springs ingest: {unparsed} of {COUNTS.named} species values "
            "unreadable; the namescientific field's format has changed"
        )
    table = validate_coordinates(table, city="Coral Springs", city_code="USCRS")
    table = enforce_tree_schema(
        table, city="Coral Springs", data_source="CORAL_SPRINGS_OPENDATA"
    )
    emit(table)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy", "tzdata"]
# ///
"""Bozeman, Montana's City-maintained trees, from the City Forestry Division's tree inventory.

LICENCE: no licence stated; displayed with attribution only, not licensed for
reuse.  The service's portal item (229be54ae989431796bc47d20b77e7aa on the
City's own portal, gisweb.bozeman.net/portal, owner `gisadmin@BOZEMAN.GOV`)
has no `licenseInfo` or `accessInformation`; the layer's copyright text is
"City of Bozeman"; the City's GIS terms are a disclaimer only
(https://www.bozemanmt.gov/departments/information-technology/disclaimer),
and the City's open-data hub publishes no tree dataset.

Source: "Tree Inventory", layer 2 of `Public/Forestry_Public_Web_Map/
MapServer` on the City's ArcGIS Server, gisweb.bozeman.net ("Forestry public
facing app"; the layer behind the Tree Inventory map embedded on the Forestry
Division's home page, web map 5980bc25acee43e08987bc47bcec583f), 28,625 rows
on 2026-11-27, stored in UTM 12N (wkid 26912) and read in WGS84
(`outSR=4326`).  It is the same feature class, a versioned enterprise
geodatabase edited daily by Forestry staff, as the lead
`Internal/Forestry_Dashboard/MapServer/0` (33,212 rows; same OBJECTIDs,
same attributes on every shared row): the internal layer's extra 4,587 rows
are `Common_Name = removal site` (4,392, trees removed and kept for the work
history), `stump` (118) and 77 with no common name and mostly no genus.  The
public layer is read because it is the published one and already leaves
those out.

**The id is `FacilityID`** (`FT_00010`, Forestry's tree number), non-null on
every row but repeated: 193 values on 476 rows, distinct trees a median 18 m
apart (most from the 2018 load).  Repeated values are read up front with one
`groupBy ... having` statistics query and those rows take
`bozeman-{FacilityID}-{OBJECTID}`, every other row `bozeman-{FacilityID}`
(Baltimore's and Columbus's rule).  There is no GlobalID in the layer.  The
OBJECTID is edited in place (max 598,149 over 33,212 rows) and is stable,
which is what makes it a fair tiebreak.  A row with no `FacilityID` is
dropped with a logged count.

**Which rows are trees.**  The public layer holds trees only, except 24 rows
whose `Genus` is `N/A` (every one a stump or snag in `Notes`) and one whose
`Genus` is `Stumpus`: both are dropped.  `Condition = Dead` (245) and trees
with a removal scheduled stay, as the schema keeps dead trees.

**Species is `Botanical_Name`**, a coded-value domain whose codes are the
binomials (`Acer x freemanii`, `Malus species`, `Ulmus davidiana var.
japonica`), read through the shared sanitiser, which cuts `species` and
varieties to genus/species rank.  It is null or blank on 5,438 rows; those
take `Genus` alone (also a domain), because the separate `Species` epithet
field is filled by the editing form's last value: where both are present it
disagrees with `Botanical_Name` on 306 of 9,716 rows, and on the rows with
no botanical name it welds genera to other genera's epithets (`Ulmus
aucuparia`, `Malus rubrum`).  Where `Genus` and `Botanical_Name` disagree
(under 50 rows) the botanical name wins.  `SPECIES_OVERRIDES` reads the
three domain codes that are not a taxon as written.  `unknown`/`unknown
unknown` is Unknown.  A `Botanical_Name` that does not read as a Latin name
counts as unreadable; above 1% the build is refused.  `Common_Name` is not
mapped.

**Cultivar is `Cultivar`** (a domain of 66 names, `Brandon`, `Canada Red`),
emitted as published.

**DBH is `DBH`, whole inches** (median 5 over trees, 99th percentile 32;
the inventory is young plantings and mature street trees).  0 is unmeasured
and nulled by the schema.

**Plant date is `Installation_Date`** (2,552 rows, 2010-2026, the City's
planting programme; median DBH 2 in at planting).  The stamps are local times
(UTC 15:00-21:00, or local midnight), so the date is read in America/Denver.
No placeholder date is stamped.  `Inventory_Date` and `Condition_Date` are
survey dates and are not read.  No height or crown spread is recorded.
"""

import json
import re
import sys
from collections import Counter
from datetime import date
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (  # noqa: E402
    FeatureLayer,
    esri_ms_to_datetime,
    esri_point,
    iter_features,
)
from shared.ingest import (  # noqa: E402
    UNKNOWN_SPECIES,
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    stream_to_table,
    validate_coordinates,
)

SOURCE_URL = (
    "https://gisweb.bozeman.net/arcgis/rest/services/Public/"
    "Forestry_Public_Web_Map/MapServer/2"
)
LAYER = FeatureLayer(SOURCE_URL, timeout=180)
OUT_FIELDS = "OBJECTID,FacilityID,Genus,Botanical_Name,Cultivar,DBH,Installation_Date"
ID_PREFIX = "bozeman"
MOUNTAIN = ZoneInfo("America/Denver")

# `Genus` values that mark a stump, not a tree.
NOT_A_TREE_GENERA = frozenset({"n/a", "stumpus"})
UNKNOWN_VALUES = frozenset({"unknown", "unknown unknown"})

# Domain codes (lower-cased) that are not the taxon as written.
SPECIES_OVERRIDES = {
    # The domain's only Douglas-fir code: the genus with Sorbus x
    # thuringiaca's epithet pasted on (`Common_Name` douglasfir on every row,
    # and the domain has no other Pseudotsuga).
    "pseudotsuga thuringiaca": "Pseudotsuga menziesii",
    # Heritage oak, bur x English (Sioux Falls's reading).
    "quercus robur x macrocarpa": "Quercus x macdanielii",
}
# `Genus` domain codes misspelt in the domain.
GENUS_OVERRIDES = {"gymnocladis": "Gymnocladus"}

MAX_UNPARSED_SHARE = 0.01

# Optional hybrid mark, genus, then lower-case words (epithet, `x`, `var.`).
LATIN = re.compile(r"^(X )?[A-Z][a-z]+( [a-z][a-z.-]*)*$")


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
    genus_only = 0


COUNTS = Counts()


@cache
def repeated_ids() -> frozenset[str]:
    """The `FacilityID` values the layer assigns to more than one row."""
    payload = get_json_with_retry(
        LAYER.query_url,
        params={
            "where": "FacilityID IS NOT NULL",
            "groupByFieldsForStatistics": "FacilityID",
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
        raise RuntimeError(f"Bozeman FacilityID duplicate query was incomplete: {payload!r}"[:500])
    return frozenset(clean(f["attributes"]["FacilityID"]) for f in payload["features"])


def keep(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    genus = (clean(attrs.get("Genus")) or "").lower()
    if genus in NOT_A_TREE_GENERA:
        COUNTS.dropped[f"Genus {genus}"] += 1
        return False
    if clean(attrs.get("FacilityID")) is None:
        COUNTS.no_id += 1
        return False
    lat, lon = esri_point(feature.get("geometry"))
    if lat is None or lon is None:
        COUNTS.no_geometry += 1
        return False
    return True


def parse_species(botanical, genus) -> str | None:
    """The botanical name as published, or the genus where it is blank.

    Examples:
        ("Acer x freemanii", "Acer")          -> "Acer x freemanii"
        ("Pseudotsuga thuringiaca", ...)       -> "Pseudotsuga menziesii"
        (None, "Tilia")                        -> "Tilia"
        ("unknown unknown", "Prunus")          -> "Unknown"
        (None, " ")                            -> None
    """
    text = clean(botanical)
    if text is not None:
        COUNTS.named += 1
        key = text.lower()
        if key in UNKNOWN_VALUES:
            return UNKNOWN_SPECIES
        if key in SPECIES_OVERRIDES:
            return SPECIES_OVERRIDES[key]
        if not LATIN.match(text):
            COUNTS.unparsed[text] += 1
            return None
        return text
    g = clean(genus)
    if g is None:
        return None
    if g.lower() in UNKNOWN_VALUES:
        return UNKNOWN_SPECIES
    g = GENUS_OVERRIDES.get(g.lower(), g)
    COUNTS.genus_only += 1
    return g


def tree_id_of(attrs: dict) -> str:
    facility = clean(attrs["FacilityID"])
    if facility in repeated_ids():
        return f"{ID_PREFIX}-{facility}-{int(attrs['OBJECTID'])}"
    return f"{ID_PREFIX}-{facility}"


def installed(value) -> date | None:
    stamp = esri_ms_to_datetime(value)
    return stamp.astimezone(MOUNTAIN).date() if stamp else None


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
    cultivar: list[str | None] = []
    planted: list[date | None] = []
    latitude: list[float] = []
    longitude: list[float] = []
    dbh: list[float | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        tree_id.append(tree_id_of(attrs))
        species.append(parse_species(attrs.get("Botanical_Name"), attrs.get("Genus")))
        cultivar.append(clean(attrs.get("Cultivar")))
        planted.append(installed(attrs.get("Installation_Date")))
        latitude.append(lat)
        longitude.append(lon)
        diameter = attrs.get("DBH")
        dbh.append(float(diameter) if diameter is not None else None)

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["USBZN"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "cultivar": pa.array(cultivar, type=pa.string()),
        "plant_date": pa.array(planted, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


if __name__ == "__main__":
    table = stream_to_table(iter_chunks(), transform, keep=keep, label="Bozeman OpenData")
    dropped = ", ".join(f"{n:,} {why}" for why, n in COUNTS.dropped.most_common())
    print(
        f"Bozeman ingest: dropped {sum(COUNTS.dropped.values()):,} rows that are "
        f"not trees ({dropped or 'none'}), {COUNTS.no_id} with no FacilityID, "
        f"{COUNTS.no_geometry} with no geometry; {len(repeated_ids())} repeated "
        f"FacilityIDs keyed with their OBJECTID; {COUNTS.genus_only:,} rows "
        "with no botanical name read at genus rank",
        file=sys.stderr,
    )
    unparsed = sum(COUNTS.unparsed.values())
    if unparsed:
        print(
            f"Bozeman ingest: {unparsed} species values unreadable: "
            f"{COUNTS.unparsed.most_common(10)}",
            file=sys.stderr,
        )
    if COUNTS.named and unparsed / COUNTS.named > MAX_UNPARSED_SHARE:
        raise RuntimeError(
            f"Bozeman ingest: {unparsed} of {COUNTS.named} species values "
            "unreadable; the Botanical_Name domain has changed"
        )
    table = validate_coordinates(table, city="Bozeman", city_code="USBZN")
    table = enforce_tree_schema(
        table, city="Bozeman", data_source="BOZEMAN_OPENDATA"
    )
    emit(table)

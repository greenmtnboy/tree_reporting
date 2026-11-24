#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Davenport, Iowa's City-maintained trees, from the City's ArcGIS Online organisation.

LICENCE: no licence stated; displayed with attribution only, not licensed for
reuse.  The item's `licenseInfo`, `accessInformation` and description are
empty (https://www.arcgis.com/home/item.html?id=1e73252e386144799b39e95dbaa684ef),
and the layer is not in the catalogue of the City's GIS hub
(https://gis-davenportiowa.hub.arcgis.com), which states no terms either.

Source: "Trees_All" (ArcGIS item 1e73252e386144799b39e95dbaa684ef, owner
`kevinrandle` of the City's own organisation `Sf3rzzad9Dl4Cuf7`, "Davenport
Iowa GIS Maps" / City of Davenport GIS; snippet "All City managed and
maintained trees in the City"), the service's only layer
`Trees_All/FeatureServer/0`: 46,562 rows on 2026-11-23, street trees
(`LOCTYPE = Street`, 31,818) and park and public-space trees.  It is the
City's tree-inventory software export (`INV_DATE` 2012-2021, `INSPECT_DT`
to 2022-01-10) loaded once in January 2022 (`editingInfo.dataLastEditDate`
2022-01-12) and not edited since; the City publishes no other tree layer
(its own server `gis.davenportiowa.com` has none, and the copies under
`jbowles_MyAGO` are an Esri demonstration account's 2016 snapshot).
Geometry is stored in Web Mercator and read in WGS84 (`outSR=4326`); the
`LATITUDE`/`LONGITUDE` attributes agree with it on every row.

**The id is `ID`** (`davenport-{ID}`), the inventory's tree number: an
integer on every row, non-null and unique over the whole layer (46,562
distinct values, 1..47,072 with gaps where trees were deleted -- not the
OBJECTID, 1..46,562 dense, which it equals on only 129 rows).  `UNIQUEID`
(`CM 20120911160638`: the surveyor's initials and the record's creation
stamp) is unique too, but is not the number a City crew quotes.  A row with
no `ID` is dropped with a logged count, and a repeated one raises (the
schema's grain check).

**Which rows are trees.**  The inventory records planting sites and stumps
in the species column: `SPP_BOT` `vacant site small|medium|large` (19,898),
`stump`, `no grind stump`, `grindings`, and five ash stumps written
`fraxinus` with `SPP_COM = ash stump` are dropped.  `COND = Dead` and
trees whose maintenance task (`MT`) is `Removal` are still standing and
stay.  `ACTIVE` is 1 on every row; another value is dropped and counted.

**Species is `SPP_BOT`**, the botanical name (`Gleditsia triacanthos
inermis`, `Acer x freemanii`, `Malus spp.`), read through the shared
sanitiser, which truncates varieties to species rank and folds the
synonyms and misspellings (`Tsuga candensis`, `Quercus prinus`,
`X Cupressocyparis leylandii`).  `unknown tree` is `Unknown`.  A value that
does not read as a Latin name counts as unreadable; above 1% the build is
refused.  `SPP_COM` (common names) is not mapped.

**DBH is `DBH`, inches** (whole inches; median 12 over trees, 99th
percentile 45, max 107); 0 is unmeasured and nulled by the schema.
`TRUNKS` is a stem count and is not read.

No planting date is recorded (`INV_DATE` is the survey date), and no height
or crown spread.
"""

import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features  # noqa: E402
from shared.ingest import (  # noqa: E402
    UNKNOWN_SPECIES,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

SOURCE_URL = (
    "https://services.arcgis.com/Sf3rzzad9Dl4Cuf7/arcgis/rest/services/"
    "Trees_All/FeatureServer/0"
)
LAYER = FeatureLayer(SOURCE_URL, timeout=180)
OUT_FIELDS = "OBJECTID,ID,ACTIVE,SPP_BOT,SPP_COM,DBH"
ID_PREFIX = "davenport"

# `SPP_BOT` values (lower-cased) that are a site or a stump, not a tree.
NOT_TREES = frozenset({
    "vacant site small",
    "vacant site medium",
    "vacant site large",
    "stump",
    "no grind stump",
    "grindings",
})
UNKNOWN_VALUES = frozenset({"unknown tree", "unknown"})

MAX_UNPARSED_SHARE = 0.01

# Optional hybrid mark, genus, optional hybrid mark, then lower-case words
# (epithet, variety, `spp.`).
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


def keep(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    botanical = (clean(attrs.get("SPP_BOT")) or "").lower()
    common = (clean(attrs.get("SPP_COM")) or "").lower()
    if botanical in NOT_TREES:
        COUNTS.dropped[botanical] += 1
        return False
    if "stump" in common:
        COUNTS.dropped[f"{common} ({botanical})"] += 1
        return False
    if attrs.get("ACTIVE") != 1:
        COUNTS.dropped[f"ACTIVE {attrs.get('ACTIVE')!r}"] += 1
        return False
    if attrs.get("ID") is None:
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
        "Gleditsia triacanthos inermis" -> "Gleditsia triacanthos inermis"
        "unknown tree"                  -> "Unknown"
        ""                              -> None
    """
    text = clean(raw)
    if text is None:
        return None
    COUNTS.named += 1
    if text.lower() in UNKNOWN_VALUES:
        return UNKNOWN_SPECIES
    if not LATIN.match(text):
        COUNTS.unparsed[text] += 1
        return None
    return text


def tree_number(value) -> str:
    number = float(value)
    if not number.is_integer():
        raise RuntimeError(f"Davenport ingest: non-integer tree ID {value!r}")
    return f"{ID_PREFIX}-{int(number)}"


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

    for feature in features:
        attrs = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        tree_id.append(tree_number(attrs["ID"]))
        species.append(parse_species(attrs.get("SPP_BOT")))
        planted.append(None)
        latitude.append(lat)
        longitude.append(lon)
        diameter = attrs.get("DBH")
        dbh.append(float(diameter) if diameter is not None else None)

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["USDVN"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "plant_date": pa.array(planted, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


if __name__ == "__main__":
    table = stream_to_table(iter_chunks(), transform, keep=keep, label="Davenport OpenData")
    dropped = ", ".join(f"{n:,} {why}" for why, n in COUNTS.dropped.most_common())
    print(
        f"Davenport ingest: dropped {sum(COUNTS.dropped.values()):,} rows that are "
        f"not trees ({dropped}), {COUNTS.no_id} with no ID, "
        f"{COUNTS.no_geometry} with no geometry",
        file=sys.stderr,
    )
    unparsed = sum(COUNTS.unparsed.values())
    if unparsed:
        print(
            f"Davenport ingest: {unparsed} species values unreadable: "
            f"{COUNTS.unparsed.most_common(10)}",
            file=sys.stderr,
        )
    if COUNTS.named and unparsed / COUNTS.named > MAX_UNPARSED_SHARE:
        raise RuntimeError(
            f"Davenport ingest: {unparsed} of {COUNTS.named} species values "
            "unreadable; the SPP_BOT field's format has changed"
        )
    table = validate_coordinates(table, city="Davenport", city_code="USDVN")
    table = enforce_tree_schema(
        table, city="Davenport", data_source="DAVENPORT_OPENDATA"
    )
    emit(table)

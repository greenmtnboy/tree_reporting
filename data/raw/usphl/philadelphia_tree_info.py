#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Philadelphia's tree inventory, from Philadelphia Parks & Recreation (PPR).

Source: the "Philadelphia Tree Inventory" on OpenDataPhilly ("A comprehensive
inventory of all trees within the limits of the City of Philadelphia ... a
snapshot in time from 2021 and will update yearly"), licence "City of
Philadelphia License" (https://metadata.phila.gov/#help/help-faqs/what-are-the-terms-of-use/).
Each year's snapshot is its own hosted layer on the City's ArcGIS Online
organisation (owner `maps.phl.data`): `PPR_Tree_Inventory_2021`,
`PPR_Tree_Inventory_2022`, `ppr_tree_inventory_2023` ... `ppr_tree_inventory_2026`
(152,413 rows, last edited 2026-11-03).

**The layer is renamed every year, so the year is discovered, not written
here.**  `latest_layer` reads the organisation's services directory and takes
the highest `ppr_tree_inventory_{year}` (case-insensitive: 2021 and 2022 are
capitalised).  The Hub items, the CARTO tables and the OpenDataPhilly
resources are per year too -- there is no "current" alias anywhere -- and the
services directory is the publisher's own list, not a catalogue that can lag
it.  The probe
discovers the year the same way, so a new year's layer is a new watermark.
A discovered layer whose fields are not the five read here raises: that is a
schema change, not a new year.

**The layer has five columns.**  `tree_name` (`ACER RUBRUM - RED MAPLE`),
`tree_dbh`, `year` (the snapshot year, the same on every row -- not a planting
year), and `loc_y`/`loc_x`, WGS84 latitude and longitude as attributes.  The
point geometry is Web Mercator, and reprojecting it moves a point by up to
1e-5 degrees against `loc_*`, so the attributes are read and the geometry is
not.  There is no planting date, no condition and no site status.

**The tree id is the rounded coordinate (`phl-{lon:.7f}_{lat:.7f}`): a
deliberate exception to "never a positional id", as for Longueuil.**  There is
no `GlobalID` and no asset number.  `objectid` is a row number within one
year's layer, and a new year is a new layer: only 83 of the 2025 layer's
objectids name the same point in 2026 -- an objectid key would re-key every
tree once a year, and with it every community check-in.  The coordinate is
what survives: 147,938 of the 2025 layer's 151,726 coordinates appear
unchanged, to the last digit, in 2026.  7 decimal places (~1 cm) is below any
real re-survey and keeps the key insensitive to a float writer's last digit.

**Stacked coordinates.**  140 coordinates carry more than one row (293 rows,
2026).  Where every row at the point has the same `tree_name` and `tree_dbh`
(56 points) they are one tree recorded twice and one row is kept.  Where they
differ (84 points: a red maple and a white ash at one point) the source has
not positioned them apart, publishing one would assert a species it does not
support, and every row at the point is dropped (Longueuil's rule).

**Species is the Latin half of `tree_name`**, split at ` - ` (four names use
an en dash).  `GENUS SPECIES` is a genus identification and is cut to the
genus by the shared schema, as are synonyms and the archaic names
(`SOPHORA JAPONICA`, `PLATANUS X ACERIFOLIA`).  A handful of Latin halves are
not names, and `LATIN_OVERRIDES` reads them: cultivars written as hybrid
epithets (`ULMUS X FRONTIER`, `PRUNUS X INCAMP` = 'Okame'), a cultivar
written as the epithet (`PRUNUS AMANOGAWA`), the common genus `POPLAR`, and
the placeholders `UNKNOWN UNKNOWN` (Unknown) and `SHRUB SHRUB` (the Shrub
sentinel).  The common-name half is never mapped to `species`.

**`cultivar` comes from the common-name half**, and only through the closed
`CULTIVARS` table: PPR names a selection by prefixing it to the species'
common name (`ACER RUBRUM - RED SUNSET RED MAPLE`, `PRUNUS SERRULATA - KWANZAN
CHERRY`).  A descriptive prefix that is not a cultivar (`WHITE EASTERN
REDBUD`, `COLUMNAR SUGAR MAPLE`, `PURPLELEAF PLUM`) is not in the table, and a
new name the table does not know publishes no cultivar rather than a guess.

**DBH is `tree_dbh`, inches** (median 8, 95th percentile 33, maximum 82 in
2026; 1,118 in 2025 was a typo the schema's 200-inch ceiling nulls).  Zeros
are nulled by the schema.

**Rows with no species and no diameter are dropped.**  `tree_name` is blank
or null on 520 rows (2026), 220 of them with `tree_dbh` = 0 and 36 with none:
nothing in such a row says a tree stands there, and the layer has no site
column to ask.  A blank name with a positive diameter is a measured,
unidentified tree and is kept as Unknown.
"""

import re
import sys
from collections import Counter
from functools import cache
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    SHRUB_SPECIES,
    coordinate_key,
    coordinate_tree_id,
    drop_stacked_coordinates,
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    normalize_species,
    validate_coordinates,
)
from shared.platforms.arcgis import FeatureLayer, iter_features, layer_metadata

SERVICES_URL = "https://services.arcgis.com/fLeGjb7u4uXqeF9q/arcgis/rest/services"
LAYER_NAME = re.compile(r"ppr_tree_inventory_(\d{4})", re.IGNORECASE)
EXPECTED_FIELDS = frozenset({"objectid", "tree_name", "tree_dbh", "loc_y", "loc_x"})

# The Latin half and the common half, split at the first spaced hyphen or en dash.
_SEPARATOR = re.compile(r"\s+[-–]\s+")

# Latin halves that are not a taxon as written: (species, cultivar).
LATIN_OVERRIDES: dict[str, tuple[str | None, str | None]] = {
    "UNKNOWN UNKNOWN": (None, None),
    "SHRUB SHRUB": (SHRUB_SPECIES, None),
    "POPLAR SPECIES": ("Populus", None),
    "ULMUS X FRONTIER": ("Ulmus", "Frontier"),
    "ULMUS X EMERALD SUNSHINE": ("Ulmus", "Emerald Sunshine"),
    "CORNUS X RUTBAN": ("Cornus", "Rutban"),
    "PRUNUS X INCAMP": ("Prunus", "Okame"),
    "PRUNUS AMANOGAWA": ("Prunus serrulata", "Amanogawa"),
}

# Full `tree_name` (whitespace collapsed, dash normalised) -> cultivar.
CULTIVARS: dict[str, str] = {
    "ACER PLATANOIDES - CRIMSON KING NORWAY MAPLE": "Crimson King",
    "ACER RUBRUM - AUTUMN FLAME RED MAPLE": "Autumn Flame",
    "ACER RUBRUM - BOWHALL RED MAPLE": "Bowhall",
    "ACER RUBRUM - KARPICK RED MAPLE": "Karpick",
    "ACER RUBRUM - OCTOBER GLORY RED MAPLE": "October Glory",
    "ACER RUBRUM - RED SUNSET RED MAPLE": "Red Sunset",
    "ACER SACCHARUM - BONFIRE SUGAR MAPLE": "Bonfire",
    "ACER SACCHARUM - GOLDSPIRE SUGAR MAPLE": "Goldspire",
    "ACER SACCHARUM - GREEN MOUNTAIN SUGAR MAPLE": "Green Mountain",
    "ACER SACCHARUM - LEGACY SUGAR MAPLE": "Legacy",
    "ACER X FREEMANII - ARMSTRONG MAPLE": "Armstrong",
    "ACER X FREEMANII - AUTUMN BLAZE MAPLE": "Autumn Blaze",
    "CARPINUS BETULUS - FASTIGIATE HORNBEAM": "Fastigiata",
    "CELTIS OCCIDENTALIS - MAGNIFICA HACKBERRY": "Magnifica",
    "CRATAEGUS VIRIDIS - WINTER KING HAWTHORN": "Winter King",
    "FRAXINUS AMERICANA - AUTUMN APPLAUSE WHITE ASH": "Autumn Applause",
    "FRAXINUS AMERICANA - AUTUMN PURPLE WHITE ASH": "Autumn Purple",
    "FRAXINUS PENNSYLVANICA - SUMMIT GREEN ASH": "Summit",
    "GINKGO BILOBA - PRINCETON SENTRY GINKGO": "Princeton Sentry",
    "GYMNOCLADUS DIOICUS - ESPRESSO KENTUCKY COFFEETREE": "Espresso",
    "LIQUIDAMBAR STYRACIFLUA - ROTUNDILOBA SWEETGUM": "Rotundiloba",
    "MACLURA POMIFERA - WHITE SHIELD OSAGE ORANGE": "White Shield",
    "MALUS SPECIES - HARVEST GOLD CRABAPPLE": "Harvest Gold",
    "MALUS SPECIES - INDIAN SUMMER CRABAPPLE": "Indian Summer",
    "MALUS SPECIES - PINK SPIRES CRABAPPLE": "Pink Spires",
    "MALUS SPECIES - SNOWDRIFT CRABAPPLE": "Snowdrift",
    "MALUS SPECIES - SPRING SNOW CRABAPPLE": "Spring Snow",
    "MALUS SPECIES - SUGAR TYME CRABAPPLE": "Sugar Tyme",
    "PLATANUS X ACERIFOLIA - BLOODGOOD LONDON PLANETREE": "Bloodgood",
    "PRUNUS SARGENTII - COLUMNAR SARGENT CHERRY": "Columnaris",
    "PRUNUS SERRULATA - KWANZAN CHERRY": "Kwanzan",
    "PRUNUS SERRULATA - MT FUJI CHERRY": "Mount Fuji",
    "PRUNUS SUBHIRTELLA - AUTUMN FLOWERING CHERRY": "Autumnalis",
    "PRUNUS SUBHIRTELLA - HIGAN PENDULA CHERRY": "Pendula",
    "PRUNUS SUBHIRTELLA - ROSY CLOUD CHERRY": "Rosy Cloud",
    "PRUNUS SUBHIRTELLA - SNOW GOOSE CHERRY": "Snow Goose",
    "PRUNUS VIRGINIANA - CANADA RED CHOKECHERRY": "Canada Red",
    "PYRUS CALLERYANA - BRADFORD PEAR": "Bradford",
    "QUERCUS ROBUR - COLUMNAR ENGLISH OAK": "Fastigiata",
    "QUERCUS X WAREI - REGAL PRINCE OAK": "Regal Prince",
    "ROBINIA PSEUDOACACIA - PURPLE ROBE BLACK LOCUST": "Purple Robe",
    "SYRINGIA RETICULATA - IVORY SILK JAPANESE TREE LILAC": "Ivory Silk",
    "TILIA CORDATA - GREENSPIRE LITTLE LEAF LINDEN": "Greenspire",
    "ULMUS AMERICANA - PRINCETON ELM": "Princeton",
    "ZELKOVA SERRATA - GREEN VASE ZELKOVA": "Green Vase",
}

# Refuse when more than this share of named rows has no ` - ` to split at:
# the naming convention has changed.
MAX_UNPARSED_SHARE = 0.01


@cache
def latest_layer() -> tuple[int, FeatureLayer]:
    """The newest year's `ppr_tree_inventory_{year}` layer, read from the services directory."""
    payload = get_json_with_retry(SERVICES_URL, params={"f": "json"}, timeout=120)
    years = {
        int(m.group(1)): service["name"]
        for service in payload.get("services") or []
        if service.get("type") == "FeatureServer"
        and (m := LAYER_NAME.fullmatch(service.get("name") or ""))
    }
    if not years:
        raise RuntimeError(f"no ppr_tree_inventory_{{year}} service in {SERVICES_URL}")
    year = max(years)
    layer = FeatureLayer(f"{SERVICES_URL}/{years[year]}/FeatureServer/0", timeout=180)
    fields = {f["name"].lower() for f in layer_metadata(layer).get("fields") or []}
    missing = EXPECTED_FIELDS - fields
    if missing:
        raise RuntimeError(f"Philadelphia {years[year]} lacks {sorted(missing)}; the schema changed")
    return year, layer


def clean_name(value) -> str:
    """`ULMUS X FRONTIER  - FRONTIER ELM` -> `ULMUS X FRONTIER - FRONTIER ELM`."""
    text = " ".join(str(value or "").split()).upper()
    return _SEPARATOR.sub(" - ", text, count=1)


def parse_name(name: str) -> tuple[str | None, str | None, bool]:
    """(species, cultivar, parsed) for one cleaned `tree_name`."""
    if not name:
        return None, None, True
    latin, sep, _ = name.partition(" - ")
    if not sep:
        return normalize_species(latin), None, False
    if latin in LATIN_OVERRIDES:
        species, cultivar = LATIN_OVERRIDES[latin]
    else:
        species, cultivar = normalize_species(latin), None
    return species, CULTIVARS.get(name) or cultivar, True


def coord_key(rec: dict) -> tuple[float, float] | None:
    lat, lon = rec.get("loc_y"), rec.get("loc_x")
    if lat is None or lon is None:
        return None
    return coordinate_key(lon, lat)


def has_diameter(dbh) -> bool:
    return dbh is not None and float(dbh) > 0


def fetch_rows() -> tuple[int, list[tuple]]:
    """(objectid, cleaned name, dbh, lon, lat) for every row of the newest layer."""
    year, layer = latest_layer()
    rows = []
    for page in iter_features(
        layer,
        out_fields="objectid,tree_name,tree_dbh,loc_y,loc_x",
        order_by="objectid",
    ):
        for feature in page:
            rec = feature.get("attributes") or {}
            key = coord_key(rec)
            rows.append(
                (rec["objectid"], clean_name(rec.get("tree_name")), rec.get("tree_dbh"),
                 key[0] if key else None, key[1] if key else None)
            )
    print(f"Philadelphia: read {len(rows):,} rows from ppr_tree_inventory_{year}", file=sys.stderr)
    return year, rows


def select_rows(rows: list[tuple]) -> list[tuple]:
    """Drop rows with no position, or no species and no diameter; in objectid order.

    Stacked points are resolved after `transform`, by `drop_stacked_coordinates`:
    the first (lowest objectid) of identical rows is kept.
    """
    dropped: Counter[str] = Counter()
    kept = []
    for row in rows:
        _, name, dbh, lon, _lat = row
        if lon is None:
            dropped["no coordinate"] += 1
        elif not name and not has_diameter(dbh):
            dropped["no species and no diameter"] += 1
        else:
            kept.append(row)

    for reason, n in sorted(dropped.items()):
        print(f"Philadelphia: dropped {n:,} rows: {reason}", file=sys.stderr)
    kept.sort()
    return kept


def transform(rows: list[tuple]) -> pa.Table:
    tree_id, species, cultivar, latitude, longitude, dbh = [], [], [], [], [], []
    unparsed = named = 0
    for _, name, raw_dbh, lon, lat in rows:
        sp, cv, parsed = parse_name(name)
        named += bool(name)
        unparsed += not parsed
        # The coordinate is the id; see the module docstring.
        tree_id.append(coordinate_tree_id("phl", lon, lat))
        species.append(sp)
        cultivar.append(cv)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(float(raw_dbh) if raw_dbh is not None else None)
    if named and unparsed / named > MAX_UNPARSED_SHARE:
        raise RuntimeError(
            f"Philadelphia: {unparsed:,} of {named:,} tree_name values have no ' - ' "
            "between the Latin and common names; the naming convention changed"
        )
    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["USPHL"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array([None] * n, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    _, source_rows = fetch_rows()
    table = transform(select_rows(source_rows))
    table = drop_stacked_coordinates(
        table, label="Philadelphia",
        identical=("species", "cultivar", "diameter_at_breast_height"),
    )
    table = validate_coordinates(table, city="Philadelphia", city_code="USPHL")
    table = enforce_tree_schema(
        table, city="Philadelphia", data_source="PHILADELPHIA_OPENDATA"
    )
    emit(table)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Eugene, OR's public tree inventory, from the city's ArcGIS Online.

Source: "Public_Trees_View" (ArcGIS item defd8da338a443818b6a6f8b65b8076b,
owner `cewmetc`, City of Eugene Parks and Open Space / Public Works Urban
Forestry), "the official non-editable view of Parks and Open Space master tree
layer": the hosted layer `Public_Trees_View_1/FeatureServer/0`, street and
park trees across the city, stored in Oregon State Plane South (ft, EPSG
2914) and reprojected server-side.  113,606 rows on 2026-11-16; the layer's
`editingInfo.dataLastEditDate` was 2026-11-15.  The item's `licenseInfo` is
only an informational-use disclaimer; the city's GIS coordinator has said the
Eugene mapping hub's data is public domain (see the attribution notes).

**Only trees are published, and `site_typ` says which.**  Its coded-value
domain is P Planting Site, S Stump, T Tree, H Historic Tree Site:

    T Tree            89,208   published
    P Planting Site   19,381   dropped (10,119 `Unknown`, 5,715 blank species)
    S Stump            5,017   dropped
    H Historic site        0   dropped if it ever appears

The domain is read at run time and the `Tree` code is kept by name, so a
renumbered domain cannot publish stumps.  The layer has no status or removal
column; a removed tree becomes a stump or planting site.

**`GlobalID` is the key** (`eug-{guid}`, lower-case): the layer's
`globalIdField`.  `site_id` is not unique (81 values repeat, 7 rows null) and
is not used.

**Species is `Tree_species`, a coded-value domain whose codes are
`Genus epithet - common name`** (`Acer rubrum - red maple`,
`Prunus spp - cherry species`), typed by hand: the stored values also carry
trailing newlines, non-breaking spaces, a left-to-right mark, an en dash for
the separator (`Liriodendron tulipifera – tulip tree`), a `×`
hybrid mark (`Platanus × acerifolia`) and curly-quoted cultivars
(`Arbutus x ‘Marina’ – Marina strawberry tree`).  So the
scientific part is everything before the *last* spaced separator (hyphen,
en dash or em dash).  An unquoted capitalised word after the genus is a
cultivar (`Arbutus x Marina`) and is quoted, so the schema moves it to
`cultivar` rather than publishing `Arbutus x marina`.  `Ulmus, spp` loses its
comma.  Three values are not in the binomial shape at all and are mapped
here: `zelkova,japanese` (231 trees, Zelkova serrata), `palm species` (31,
the Palm sentinel) and `Other - See Notes` (96, Unknown).  The common-name
half is never mapped.

**DBH is `diameter`, inches**: mode 1 (new plantings), median about 8,
1,526 null and 84 zero (nulled by the schema, as are the six over 200).
**`spread` is crown spread in feet** (recorded in 5-ft steps: 20 is the
mode at 7,553, then 15 and 25) and is published as `crown_width_m`; the
schema nulls the zero and negative values and the handful over 40 m
(1,821 ft, 25,247 ft).  `height` (feet) has no column in the schema.

**`Date_Planted` is `plant_date`**, filled on about a fifth of the layer.
Most dates are 1 January of a year (2018-01-01 is the commonest, 1,610 rows)
-- a planting recorded to the year, the same precision Copenhagen and
Helsinki publish -- and none is a stamped default, so none is nulled here.
The stamps are Pacific local times: the year cohorts are stored as
`YYYY-01-02T00:00Z` (4 pm on 1 January in Eugene) and spring plantings at
07:00Z (local midnight in daylight time), so reading them as UTC dates would
move every cohort to 2 January.  They are read at UTC-7, which gives the
local date for every stamp in the layer (all fall between 00:00Z and 23:59Z
as local times from 17:00 the day before to 16:59) whether the date was in
standard or daylight time.

Every tree with a geometry lies inside `CITY_BOUNDS["USEUG"]` (westernmost
-123.2092, easternmost -123.0003; the layer does not reach Springfield).  One
tree has no geometry and is dropped by the coordinate check, so 89,207 are
published.
"""

import re
import sys
from datetime import date, timedelta
from functools import cache
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    coded_value_domain,
    esri_ms_to_datetime,
    esri_point,
    iter_features,
)
from shared.ingest import (
    PALM_SPECIES,
    UNKNOWN_SPECIES,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services3.arcgis.com/F7NiRLGNbA2hh7gE/arcgis/rest/services/"
    "Public_Trees_View_1/FeatureServer/0"
)

OUT_FIELDS = "OBJECTID,GlobalID,site_typ,Tree_species,diameter,spread,Date_Planted"

# `site_typ` domain name that is a standing tree.
TREE_SITE_TYPE = "Tree"

FEET_TO_M = 0.3048

# `Date_Planted` is a Pacific local time; see the module docstring.
PACIFIC_READ_OFFSET = timedelta(hours=-7)

# Values outside the "Genus epithet - common name" shape, by their cleaned
# lower-case text.
NON_BINOMIAL_VALUES = {
    "zelkova,japanese": "Zelkova serrata",
    "palm species": PALM_SPECIES,
    "other - see notes": UNKNOWN_SPECIES,
}

# Characters that are not text: a non-breaking space (as a space), a
# left-to-right mark and zero-width spaces (dropped).
_INVISIBLE = str.maketrans({"\xa0": " ", "\u200e": None, "\u200b": None, "\ufeff": None})

# The separator between the scientific and the common name: a hyphen, en dash
# or em dash with whitespace on both sides.
_SEPARATOR = re.compile(r"\s[-\u2013\u2014]\s")


@cache
def tree_site_code() -> str:
    """The `site_typ` code whose domain name is `Tree`."""
    domain = coded_value_domain(LAYER, "site_typ")
    codes = [code for code, name in domain.items() if name == TREE_SITE_TYPE]
    if len(codes) != 1:
        raise RuntimeError(f"Eugene site_typ domain has no single {TREE_SITE_TYPE!r}: {domain}")
    return str(codes[0])


def clean_text(value) -> str:
    return " ".join(str(value or "").translate(_INVISIBLE).split())


def scientific_part(value) -> str | None:
    """The species string an Eugene `Tree_species` value names, cultivar quoted.

    Examples:
        "Acer rubrum - red maple"                            -> "Acer rubrum"
        "Platanus × acerifolia - London planetree"           -> "Platanus × acerifolia"
        "Liriodendron tulipifera – tulip tree"               -> "Liriodendron tulipifera"
        "Arbutus x ‘Marina’ – Marina strawberry tree"
                                                             -> "Arbutus x ‘Marina’"
        "Arbutus x Marina - Marina strawberry madrone"       -> "Arbutus x 'Marina'"
        "Acer x freemanii 'Armstrong' - Freeman Armstrong maple"
                                                             -> "Acer x freemanii 'Armstrong'"
        "Ulmus, spp - elm species"                           -> "Ulmus spp"
        "zelkova,japanese"                                   -> "Zelkova serrata"
        "Other – See Notes"                                  -> "Unknown"
    """
    text = clean_text(value)
    if not text:
        return None
    dashed = _SEPARATOR.sub(" - ", text).lower()
    if dashed in NON_BINOMIAL_VALUES:
        return NON_BINOMIAL_VALUES[dashed]
    if text.lower() in NON_BINOMIAL_VALUES:
        return NON_BINOMIAL_VALUES[text.lower()]
    matches = list(_SEPARATOR.finditer(text))
    if matches:
        text = text[: matches[-1].start()].strip()
    text = text.replace(",", " ")
    tokens = text.split()
    if not tokens:
        return None
    # An unquoted capitalised word after the genus is a cultivar name.  The
    # genus follows a leading hybrid mark (`x Chitalpa tashkentensis`).
    genus_at = 1 if tokens[0].lower() in ("x", "×") else 0
    for i, tok in enumerate(tokens[genus_at + 1 :], start=genus_at + 1):
        if tok[:1] in "'\"\u2018\u201c":
            break
        if re.fullmatch(r"[A-Z][a-z]+", tok):
            tokens[i] = "'" + " ".join(tokens[i:]) + "'"
            tokens = tokens[: i + 1]
            break
    return " ".join(tokens)


def planted_on(value) -> date | None:
    """The local date of an Esri `Date_Planted` stamp, read at UTC-7."""
    stamp = esri_ms_to_datetime(value)
    return (stamp + PACIFIC_READ_OFFSET).date() if stamp else None


def is_tree(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    return str(attrs.get("site_typ") or "").strip() == tree_site_code()


def iter_row_chunks():
    """One ArcGIS page at a time, geometry reprojected to WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        raw_id = str(rec.get("GlobalID") or "").strip().strip("{}").lower()
        if not raw_id:
            print(
                f"Eugene: skipping OBJECTID {rec.get('OBJECTID')} with no GlobalID",
                file=sys.stderr,
            )
            continue
        lat, lon = esri_point(feature.get("geometry"))

        tree_id.append(f"eug-{raw_id}")
        species.append(scientific_part(rec.get("Tree_species")))
        plant_date.append(planted_on(rec.get("Date_Planted")))
        latitude.append(lat)
        longitude.append(lon)
        raw_dbh = rec.get("diameter")
        dbh.append(float(raw_dbh) if raw_dbh is not None else None)
        raw_spread = rec.get("spread")
        crown.append(float(raw_spread) * FEET_TO_M if raw_spread is not None else None)

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["USEUG"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            "crown_width_m": pa.array(crown, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, keep=is_tree, label="Eugene OpenData")
    table = validate_coordinates(table, city="Eugene", city_code="USEUG")
    table = enforce_tree_schema(table, city="Eugene", data_source="EUGENE_OPENDATA")
    emit(table)

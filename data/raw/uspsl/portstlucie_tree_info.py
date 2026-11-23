#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Port St. Lucie, FL's street and public-grounds tree inventory, from the
City of Port St. Lucie's ArcGIS Online.

LICENCE: no licence stated; displayed with attribution only, not licensed for
reuse.  The service item ("PSL Trees", ArcGIS item
767ea62a6ae34d898185ec5050319c0d, owner `pslgisAdmin`, "Trees and bushes
throughout Port St Lucie") has empty `licenseInfo` and `accessInformation`
and is shared publicly but to none of the City's open-data groups; the City's
open data hub (opendata-pslgis.hub.arcgis.com) states no licence or terms, and
cityofpsl.com's Website Disclaimer & Usage Statement says only that "any use
or re-use of this information will be at the user's risk".

Source: layer 2 (`Trees`) of `PSL_Trees/FeatureServer`, 35,104 points on
2026-11-22, stored in NAD83 (EPSG 4269) and requested as WGS84.  The service's
other layers are not trees: `Bushes` (3, 5,495 points) and `BushArea` (4,
polygons) are shrubs and hedges, `all` (0) is `Trees` plus `Bushes`, and
`by_street` (1) is multipoints grouped by street.  Every row's `TreeShrub` is
`Tree`.  The rows are the City's managed trees along its arterials, parkways
and stormwater (EWIP) projects and at its facilities (`Street` names
`Crosstown Pkwy`, `Becker Rd`, `City Hall`, `Cane Slough EWIP`, ...).  The
City's other tree service, `CityTrees` (115,682 points, "PANDA and PW
trees"), is a planting tally for the City's tree challenge: 109,344 rows are
named only `Panda Tree`, the rest are giveaway fruit trees (avocado, star
fruit, mango) with no size, so it is not an inventory and is not read.

**The id is the rounded coordinate (`portstlucie-{lon:.7f}_{lat:.7f}`)**, as
for Spokane and Philadelphia (EXTENDING.md, step 1).  The layer has no
GlobalID and no asset id: its columns are position, `Trunks`, `DBH`,
`Height`, an appraised `Value`, `Inspected`, `Condition`, `Street`, the two
names and reference links.  `OBJECTID` runs 41,528..76,631 without a gap,
which is a layer reloaded whole (the numbering does not start at 1 because
earlier loads were truncated), so a reload renumbers every tree while a
position survives it.  72 rows share a 7 dp coordinate with another (2026-11-22):
at 7 points the rows agree on species and DBH, one tree recorded twice, and
one is kept; the 58 rows at the other 29 points disagree and are all dropped
by `drop_stacked_coordinates` with a logged count, as the id cannot tell them
apart.  35,039 trees are published.  The
geometry is used, not the `Latitude`/`Longitude` attributes, which are
rounded to 4 dp and are zero on some rows.

**`species` is `BotanicalName`**, which carries a binomial on 80% of rows
(`Sabal palmetto`, `Quercus virginiana`, `Pinus elliottii var. densa`,
`Ilex attenuata 'East Palatka'`); `enforce_tree_schema` truncates the variety,
moves the cultivar and folds the misspellings it knows.  The hybrid sign of
`Bauhinia × blakeana` arrives as U+FFFD and is restored as `x`, and the
East Palatka holly's missing one is added (`HYBRID_NAMES`).  Where it is
blank or holds a non-name (`#N/A`, `0`), the row's `CommonName` is resolved through `shared.species.english` -- the only place a
common name is read; an unresolved common name publishes `Unknown`, and more
than 1% of rows doing so refuses the run, so a new name in a republished layer
is noticed.  Palms are named to species like any other tree.

**`DBH` is diameter at breast height in whole inches** (live oaks at a median
of 12, Sabal palms at 12-14); 0 and the few garbage values (1,418, 4,075) are
nulled by the schema.  `Height` is feet and has no column in the schema;
`Trunks`, `Value`, `Condition` and `Inspected` (an inspection date, not a
planting date) are not mapped.  There is no planting date and no crown
spread.  `Dead` trees (132, `Condition`) stay: a dead tree is a tree.  The
layer carries no status column for removed trees or empty sites.
"""

import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    coordinate_key,
    coordinate_tree_id,
    drop_stacked_coordinates,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.species.english import species_from_common_name

LAYER = FeatureLayer(
    "https://services1.arcgis.com/YdUP5V6WwzeG8T8r/arcgis/rest/services/"
    "PSL_Trees/FeatureServer/2",
    timeout=300,
)
SOURCE_URL = LAYER.base

OUT_FIELDS = "OBJECTID,DBH,CommonName,BotanicalName"

# `BotanicalName` values that are not a name at all.
NOT_A_NAME = frozenset({"#n/a", "0", "n/a", "na", "none", "unknown"})

# Nothospecies the layer writes without the hybrid sign.  `Ilex attenuata`
# (the East Palatka holly) is an illegitimate name in WCVP for another holly;
# the tree is the accepted hybrid `Ilex x attenuata`.
HYBRID_NAMES = {"Ilex attenuata": "Ilex x attenuata"}

# Common names that say the species is not known; these publish `Unknown`
# honestly and are not counted against the resolution guard.
UNKNOWN_COMMON = frozenset({"unknown"})

# Refuse a run where more than this share of rows has neither a botanical
# name nor a common name `shared.species.english` resolves.
MAX_UNRESOLVED_SHARE = 0.01

DROPPED: Counter = Counter()
UNRESOLVED: Counter = Counter()


def clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\xa0", " ").replace("�", " x ")
    text = " ".join(text.split())
    return text or None


def species_of(rec: dict) -> str | None:
    common = clean(rec.get("CommonName"))
    botanical = clean(rec.get("BotanicalName"))
    if botanical is not None:
        lowered = botanical.lower()
        if lowered in NOT_A_NAME:
            botanical = None
        elif lowered == (common or "").lower():
            # The same text in both columns: a common name (`Wax Myrtle`) the
            # English table resolves, else a genus (`Eugenia sp.`) kept as is.
            return species_from_common_name(common) or botanical
        else:
            for bare, hybrid in HYBRID_NAMES.items():
                if lowered.startswith(bare.lower()):
                    return hybrid + botanical[len(bare):]
            return botanical
    if common is None:
        UNRESOLVED["<blank>"] += 1
        return None
    resolved = species_from_common_name(common)
    if resolved is None and common.lower() not in UNKNOWN_COMMON:
        UNRESOLVED[common] += 1
    return resolved


def keep(feature: dict) -> bool:
    if esri_point(feature.get("geometry")) == (None, None):
        DROPPED["no location"] += 1
        return False
    return True


def iter_row_chunks():
    """One ArcGIS page at a time, geometry reprojected to WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        lon, lat = coordinate_key(lon, lat)
        # The coordinate is the id; see the module docstring.
        tree_id.append(coordinate_tree_id("portstlucie", lon, lat))
        species.append(species_of(rec))
        latitude.append(lat)
        longitude.append(lon)
        raw_dbh = rec.get("DBH")
        dbh.append(float(raw_dbh) if raw_dbh is not None else None)

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["USPSL"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array([None] * n, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


def check_resolution(table: pa.Table) -> None:
    unresolved = sum(UNRESOLVED.values())
    if unresolved:
        top = UNRESOLVED.most_common(25)
        print(
            f"Port St. Lucie: {unresolved} rows with no botanical name and an "
            f"unresolved common name: {top}",
            file=sys.stderr,
        )
    if table.num_rows and unresolved / table.num_rows > MAX_UNRESOLVED_SHARE:
        raise RuntimeError(
            f"Port St. Lucie: {unresolved} of {table.num_rows} rows have no botanical "
            f"name and a common name shared.species.english does not resolve "
            f"(> {MAX_UNRESOLVED_SHARE:.0%}); add the names to COMMON_NAME_SPECIES"
        )


if __name__ == "__main__":
    table = stream_to_table(
        iter_row_chunks(), transform, keep=keep, label="Port St. Lucie OpenData"
    )
    if DROPPED:
        print(f"Port St. Lucie: dropped rows {dict(DROPPED)}", file=sys.stderr)
    check_resolution(table)
    table = drop_stacked_coordinates(
        table,
        label="Port St. Lucie OpenData",
        identical=("species", "diameter_at_breast_height"),
    )
    table = validate_coordinates(table, city="Port St. Lucie", city_code="USPSL")
    table = enforce_tree_schema(
        table, city="Port St. Lucie", data_source="PORT_ST_LUCIE_OPENDATA"
    )
    emit(table)

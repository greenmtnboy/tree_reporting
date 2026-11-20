#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Syracuse, NY's street-tree inventory, from the Department of Parks &
Recreation's Forestry division.

Licence: the City's Open Data Syracuse Terms of Use
(https://data.syr.gov/pages/termsofuse), which every Syracuse tree item links
as its licence: the City "grants users of City Datasets permission to
reproduce materials published by the City on Open Data Syracuse so long as the
City is noted as the source", with the access and publication dates, and asks
every work product to carry "The City of Syracuse makes no representation,
warranty or guarantee relating to the data or analyses derived from these
data."  No named open licence.

**Source: the live TreeKeeper inventory, not the ArcGIS layer.**  The City's
Forestry staff (and Cornell Cooperative Extension's CommuniTree Stewards) keep
the inventory in Davey TreeKeeper.  The City publishes it two ways:

* "Syracuse Tree Inventory" (ArcGIS item 7982e38ea4b545289736a1f42e413632,
  owner `admin_syr`, `Syracuse_Tree_Data/FeatureServer/0`): a one-off export,
  55,676 rows, last edited 2023-07-21.  Not read -- it is a stale copy.
* "Treekeeper Inventory WFS" (ArcGIS web map 175e28daaf7f4b78b58554fe8fc6117e,
  owner `general_syr`, the same data dictionary and the same Terms of Use):
  TreeKeeper's own GeoServer WFS, `syracuseNY_WFS` with the project's
  `viewparams=projectid:ThankfulTrain402`.  61,900 sites on 2026-11-20 (47,308 trees once
  sites are dropped) and edited daily (`InventoryDate` up to 2026-11-19).  This is what is read.

Without the `viewparams` the layer answers zero features, so a run that reads
none raises rather than publishing an empty city -- the project id changing is
the likeliest way this ingest breaks.

**The id is `SiteID`** (`syr-{SiteID}`): TreeKeeper's site number, an integer
on every row, unique over the whole layer (61,900 distinct of 61,900) and the
same number GeoServer uses as the feature id.

**Nearly a quarter of the layer is not a tree.**  TreeKeeper writes the site's state
into the species columns, `SpeciesBotanical` and `SpeciesCommon`:
`Planting Site - Needs Approval` (10,700), `Planting site` and its
do-not-plant variants (`Planting site - DNP`, `... DNP no room`,
`... DNP Property Owner Rejection`, `... DNP Utility conflict`,
`... DNP Hold`, `Planting Site - CU Soil`), `Stump`, `Stump - Do not grind`,
`Stump Ash`, `Vacant Lot-city owned`, `Butt Log` (a felled trunk) and
`Vegetation` (brush, not a tree at a point).  Those rows are dropped with a
count; more than `MAX_SITE_SHARE` of the layer dropped raises, because it
would mean the labels changed meaning.  There is no status or removed column.

**Species is `SpeciesBotanical`**, a free scientific name with cultivars in
quotes (`Syringa reticulata 'Ivory Silk'`).  `SPECIES_OVERRIDES` reads the
values that are not a taxon as written: unquoted cultivars (`Malus Cortland`,
`Ulmus x Accolade`, `Celtis Magnifica`, `Ulmus prop-emerald shine` -- U. propinqua 'Emerald
Sunshine', published as U. davidiana, the name Kew accepts), the
hybrid epithets written as cultivars (`Magnolia x 'Soulangiana'`,
`Laburnum x 'Watereri'`), `Tilia euchlora Crimean` (a common name trailing
the binomial), `Quercus robur x macrocarpa` (Q. x macdanielii, which the shared
rules would cut to Q. robur), and the sentinels: `Private Tree` (a tree the
City does not own, unnamed), `VARIOUS`, `Unknown spp.`, `unknown tree` ->
Unknown; `Shrub spp.` -> Shrub.  `SpeciesCommon` is not read.

**DBH is `DBH`, inches** (tree rows: median 8, 95th percentile 29, max 69);
zero (unmeasured, and every planting site) and the one negative value are
nulled by the schema.

**`plant_date` is `PlantingDate`**, a season and year (`Fall 2024`,
`Spring 2013`) or `To Be Determined` / `N/A`.  A season is published as the
first of its planting month -- April for spring, October for fall -- so the
year is right and the two seasons of one year stay ordered.  Anything else is
an unreadable value: counted, and more than 1% of rows raises.

`InventoryDate` (the day the site was entered or re-inspected) is the
freshness stamp, not a planting date.  The layer records no height or crown
spread.
"""

import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (  # noqa: E402
    SHRUB_SPECIES,
    UNKNOWN_SPECIES,
    emit,
    enforce_tree_schema,
    extract_cultivar,
    is_not_a_tree,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)
from shared.platforms.wfs import WfsLayer, iter_wfs_features  # noqa: E402

WFS_URL = "https://geocomyt.daveytreekeeper.com/geoserver/syracuseNY_WFS/wfs"
PROJECT_ID = "ThankfulTrain402"


@dataclass(frozen=True)
class TreeKeeperWfs(WfsLayer):
    """A TreeKeeper GeoServer layer: every request carries the project id."""

    view_params: str = ""

    def params(self, **extra) -> dict[str, str]:
        params = super().params(**extra)
        params["viewparams"] = self.view_params
        return params


LAYER = TreeKeeperWfs(
    WFS_URL, "syracuseNY_WFS", timeout=300, view_params=f"projectid:{PROJECT_ID}"
)
PROPERTIES = ["SiteID", "SpeciesBotanical", "DBH", "PlantingDate", "geom"]
PAGE_SIZE = 20000

# `SpeciesBotanical` values that are a site, a stump or a log rather than a
# tree, besides what `is_not_a_tree` already recognises (planting sites,
# stumps, vacant lots).
NOT_A_TREE = frozenset({"butt log", "vegetation"})

# `SpeciesBotanical` values that are not a taxon as written: (species, cultivar).
SPECIES_OVERRIDES: dict[str, tuple[str | None, str | None]] = {
    "private tree": (UNKNOWN_SPECIES, None),
    "various": (UNKNOWN_SPECIES, None),
    "unknown spp.": (UNKNOWN_SPECIES, None),
    "unknown tree": (UNKNOWN_SPECIES, None),
    "shrub spp.": (SHRUB_SPECIES, None),
    "malus cortland": ("Malus domestica", "Cortland"),
    "ulmus x accolade": ("Ulmus", "Accolade"),
    "celtis magnifica": ("Celtis", "Magnifica"),
    # Ulmus propinqua 'JFS-Bieberich'; Kew lists U. propinqua as illegitimate,
    # the taxon being U. davidiana var. japonica.
    "ulmus prop-emerald shine": ("Ulmus davidiana", "Emerald Sunshine"),
    "magnolia x 'soulangiana'": ("Magnolia x soulangeana", None),
    "laburnum x 'watereri'": ("Laburnum x watereri", None),
    "tilia euchlora crimean": ("Tilia x euchlora", None),
    "quercus robur x macrocarpa": ("Quercus x macdanielii", None),
}

# 23.6% of the layer (14,592 of 61,900) was sites, stumps and logs on 2026-11-20.
MAX_SITE_SHARE = 0.4
# Share of rows whose `PlantingDate` is neither a season nor a placeholder.
MAX_UNREADABLE_DATE_SHARE = 0.01

SEASON_MONTH = {"spring": 4, "fall": 10}
SEASON = re.compile(r"^(spring|fall)\s+(\d{4})$", re.IGNORECASE)
DATE_PLACEHOLDERS = frozenset({"", "to be determined", "n/a"})


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_species(raw) -> tuple[str | None, str | None]:
    text = clean(raw)
    if text is None:
        return UNKNOWN_SPECIES, None
    key = text.lower()
    if key in SPECIES_OVERRIDES:
        return SPECIES_OVERRIDES[key]
    return normalize_species(text), extract_cultivar(text)


class Syracuse:
    def __init__(self) -> None:
        self.seen = 0
        self.dropped: Counter[str] = Counter()
        self.unreadable_dates: Counter[str] = Counter()

    def keep(self, feature: dict) -> bool:
        self.seen += 1
        props = feature.get("properties") or {}
        label = clean(props.get("SpeciesBotanical"))
        if label is not None and (is_not_a_tree(label) or label.lower() in NOT_A_TREE):
            self.dropped["planting site, stump, log or vacant lot"] += 1
            return False
        if props.get("SiteID") is None:
            self.dropped["no SiteID"] += 1
            return False
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates") if geometry.get("type") == "Point" else None
        if not coords or len(coords) < 2:
            self.dropped["no point geometry"] += 1
            return False
        return True

    def plant_date(self, value) -> date | None:
        text = clean(value) or ""
        if text.lower() in DATE_PLACEHOLDERS:
            return None
        match = SEASON.match(text)
        if not match:
            self.unreadable_dates[text] += 1
            return None
        return date(int(match.group(2)), SEASON_MONTH[match.group(1).lower()], 1)

    def __call__(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        plant_date: list[date | None] = []
        latitude: list[float] = []
        longitude: list[float] = []
        dbh: list[float | None] = []

        for feature in features:
            props = feature.get("properties") or {}
            lon, lat = feature["geometry"]["coordinates"][:2]
            name, selection = parse_species(props.get("SpeciesBotanical"))
            raw_dbh = props.get("DBH")
            tree_id.append(f"syr-{int(props['SiteID'])}")
            species.append(name)
            cultivar.append(selection)
            plant_date.append(self.plant_date(props.get("PlantingDate")))
            latitude.append(float(lat))
            longitude.append(float(lon))
            dbh.append(float(raw_dbh) if raw_dbh is not None else None)

        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["USSYR"] * n, type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "plant_date": pa.array(plant_date, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            }
        )

    def report(self) -> None:
        if not self.seen:
            raise RuntimeError(
                f"Syracuse: {LAYER.type_name} returned no features; has the TreeKeeper "
                f"project id ({PROJECT_ID}) changed?  Read it from the City's "
                "'Treekeeper Inventory WFS' web map (175e28daaf7f4b78b58554fe8fc6117e)"
            )
        for reason, n in sorted(self.dropped.items()):
            print(f"Syracuse: dropped {n:,} rows: {reason}", file=sys.stderr)
        sites = self.dropped["planting site, stump, log or vacant lot"]
        if sites / self.seen > MAX_SITE_SHARE:
            raise RuntimeError(
                f"Syracuse: {sites:,} of {self.seen:,} rows are sites, stumps or logs; "
                "the species labels have changed meaning"
            )
        unreadable = sum(self.unreadable_dates.values())
        if unreadable:
            print(
                f"Syracuse: {unreadable:,} unreadable PlantingDate values published as null: "
                f"{dict(self.unreadable_dates.most_common(10))}",
                file=sys.stderr,
            )
        if unreadable / self.seen > MAX_UNREADABLE_DATE_SHARE:
            raise RuntimeError(
                f"Syracuse: {unreadable:,} of {self.seen:,} PlantingDate values are neither "
                "a season nor a placeholder; read the column again"
            )


def iter_row_chunks():
    """One WFS page at a time, sorted on the site id, geometry in WGS84."""
    return iter_wfs_features(
        LAYER, sort_by="SiteID", properties=PROPERTIES, page_size=PAGE_SIZE
    )


if __name__ == "__main__":
    syracuse = Syracuse()
    table = stream_to_table(
        iter_row_chunks(), syracuse, keep=syracuse.keep, label="Syracuse TreeKeeper"
    )
    syracuse.report()
    table = validate_coordinates(table, city="Syracuse", city_code="USSYR")
    table = enforce_tree_schema(table, city="Syracuse", data_source="SYRACUSE_OPENDATA")
    emit(table)

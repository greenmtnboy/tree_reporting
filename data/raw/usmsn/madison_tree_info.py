#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Madison, WI's street tree inventory, from the City's Urban Forestry section.

Source: `Public/OPEN_DATA/MapServer/0` ("Street Trees") on the City's own
ArcGIS Server, maps.cityofmadison.com -- ArcGIS item
b700541a20e446839b18d62426c266a3 ("Urban Forestry Street Trees", owner
`CityOfMadison`, access information "City of Madison Parks Department"):
"the locations of right of way trees maintained by the City of Madison Urban
Forestry Department".  111,465 rows on 2026-11-19, stored in Wisconsin County
Coordinate System feet (wkid 103412) and reprojected to WGS84 by the server.
Park trees are not in it; the City publishes no park inventory.

Licence: the item's `licenseInfo` links the City of Madison Data Policy
(https://www.cityofmadison.com/policy/data), which is a disclaimer; the terms
are the City's Open Data ordinance, Madison General Ordinances 3.72(3)(e):
"public data sets shall be made available without any registration
requirement, license requirement or restrictions on their use provided that
the department may require a third party providing to the public any public
data set, or application utilizing such data set, to explicitly identify the
source and version of the public data set, and a description of any
modifications made to such public data set."  So: attribute the City and
the dataset.

**One row is one planting site, and the species field says whether a tree is
on it.**  `SPP_BOT` (free text, no domain; `SPP_COM` is its common name and is
not mapped) holds, besides the taxa, `Vacant` (7,592), `Plat Assessable` (364:
a site on a new plat, to be planted at the developer's cost; DBH 0 or 1.5),
`TBD` (355, common name "REPL TO BE DETERMINED": a replacement not yet
chosen), `NR` (161, "No Replacement"), `Cancelled Site (see notes)` (87) and
`Stump` (12).  All are dropped.  `STATUS` is "Site active yes/no" (the item's
field list), a property of the site: 552 of its 575 `Not Active` rows are
those site codes, and the other 23 carry a species and a measured DBH (mostly
1.5-2 in. new plantings), so they are kept, as are the 1,208 blank-status
rows (293 `Vacant`, the rest trees).  `Unknown Tree`, `Other` and `Z Add 05`
(a placeholder code, 2 rows with DBH 2 and 15) are trees with no name and
publish as Unknown.

**The id is `site_id`** ("Unique ID for planting site"), non-null on every
row and repeated on none (a `groupBy site_id having count > 1` statistics
query returns nothing); the ingest re-checks both, and `enforce_tree_schema`
refuses a repeat.  There is no GlobalID.  The prefix is `msn-`.

**Species is `SPP_BOT`**, a scientific name with the cultivar quoted
(`Gleditsia triacanthos 'Skyline'`) -- the shared schema splits the cultivar
off and cuts `Species`, `var.` and `Variant`.  `NAME_FIXES` repairs the names
the source mistyped or wrote without a genus (`bushii 'Aaron1'`, `sylvatica
'Hayman Red'`, `Platanus acerfolia`, `Aesculus tataricum 'GarAnn'`, which is
the Hot Wings maple); each is read with its common name beside it.

**DBH is `DIAMETER`, inches** (median 11, 95th percentile 29, maximum 80 on
rows above zero); the site codes carry 0, which the schema nulls.  The layer
records no planting date: `INV_DATE` is the last inventory and `INSPECT_DT`
the last inspection.
"""

import json
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    UNKNOWN_CONIFER_SPECIES,
    emit,
    enforce_tree_schema,
    extract_cultivar,
    get_json_with_retry,
    is_not_a_tree,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://maps.cityofmadison.com/arcgis/rest/services/Public/OPEN_DATA/MapServer/0",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,site_id,SPP_BOT,DIAMETER"

# `SPP_BOT` values that record a site with no tree on it (module docstring).
NOT_A_TREE_SPP = frozenset(
    {
        "vacant",
        "plat assessable",
        "tbd",
        "nr",
        "cancelled site (see notes)",
        "stump",
    }
)

# The name part of `SPP_BOT` (before any quoted cultivar), lower-cased, where
# the source wrote it wrong: (species, cultivar).  A cultivar here overrides
# the quoted one; None keeps whatever `extract_cultivar` finds.  The common
# name each row carries is in the comment.
NAME_FIXES: dict[str, tuple[str, str | None]] = {
    "platanus acerfolia": ("Platanus x acerifolia", None),  # London Planetree
    "aesculus tataricum": ("Acer tataricum", None),  # Tatarian Maple 'Hot Wings'
    "bushii": ("Aesculus x bushii", None),  # Buckeye 'Mystic Ruby'
    "styracifua": ("Liquidambar styraciflua", "Moraine"),  # Sweetgum 'Moraine'
    "tulipifera": ("Liriodendron tulipifera", None),  # Tulip Poplar 'Emerald City'
    "sylvatica": ("Nyssa sylvatica", None),  # Black Gum 'Hayman Red'
    "gymnogladus dioicus": ("Gymnocladus dioicus", None),
    "aesculus hippocastenacea": ("Aesculus hippocastanum", None),
    "aesculus x homestead": ("Aesculus", "Homestead"),  # Buckeye 'Homestead'
    "celtis occidentalis jfs-ksui": ("Celtis occidentalis", "JFS-KSUI"),
    # 'Long' (Regal Prince) is Quercus robur x bicolor, named Quercus x warei.
    "quercus robur x bicolor": ("Quercus x warei", None),
    # 'Harvest Gold' is a Tilia cordata x mongolica cross with no species name.
    "tilia cordata x mongolica": ("Tilia", None),
    "arborvitae": ("Thuja", None),
    "yew species": ("Taxus", None),
    # A family, recorded where the genus was not; the common name is
    # "Spruce Spp." but the scientific field does not say so.
    "pinaceae species": (UNKNOWN_CONIFER_SPECIES, None),
}

# Refuse when more than this share of the layer is dropped as a site: the
# 2026 layer is 7.6% sites, and a jump means `SPP_BOT` changed meaning.
MAX_SITE_SHARE = 0.25


def spp_key(value) -> str:
    return " ".join(str(value or "").split()).lower()


def name_part(value: str) -> str:
    """`SPP_BOT` up to its quoted cultivar, normalised for `NAME_FIXES`."""
    cut = value.find("'")
    return spp_key(value[:cut] if cut != -1 else value)


def check_site_ids() -> None:
    """Refuse a layer whose `site_id` is no longer a key."""
    payload = get_json_with_retry(
        LAYER.query_url,
        params={
            "where": "site_id IS NOT NULL",
            "groupByFieldsForStatistics": "site_id",
            "outStatistics": json.dumps(
                [{"statisticType": "count", "onStatisticField": "OBJECTID",
                  "outStatisticFieldName": "n"}]
            ),
            "having": "COUNT(OBJECTID) > 1",
            "f": "json",
        },
        timeout=LAYER.timeout,
    )
    if "features" not in payload:
        raise RuntimeError(f"Madison site_id duplicate query failed: {payload!r}"[:500])
    if payload["features"]:
        repeated = [f["attributes"]["site_id"] for f in payload["features"][:10]]
        raise RuntimeError(
            f"Madison: site_id repeats on {len(payload['features'])} values "
            f"(e.g. {repeated}); it is no longer the layer's key"
        )


class Madison:
    def __init__(self) -> None:
        self.dropped: Counter[str] = Counter()
        self.seen = 0

    def keep(self, feature: dict) -> bool:
        self.seen += 1
        rec = feature.get("attributes") or {}
        key = spp_key(rec.get("SPP_BOT"))
        if key in NOT_A_TREE_SPP or is_not_a_tree(rec.get("SPP_BOT")):
            self.dropped["site with no tree (SPP_BOT)"] += 1
            return False
        if not key and not (rec.get("DIAMETER") or 0) > 0:
            self.dropped["no species and no diameter"] += 1
            return False
        if rec.get("site_id") is None:
            self.dropped["no site_id"] += 1
            return False
        return True

    def __call__(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            tree_id.append(f"msn-{int(rec['site_id'])}")
            raw = " ".join(str(rec.get("SPP_BOT") or "").split())
            fix = NAME_FIXES.get(name_part(raw))
            parsed_cv = extract_cultivar(raw)
            if fix is not None:
                sp, cv = fix
                species.append(sp)
                cultivar.append(cv or parsed_cv)
            else:
                species.append(raw or None)
                cultivar.append(parsed_cv)
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            raw_dbh = rec.get("DIAMETER")
            dbh.append(float(raw_dbh) if raw_dbh is not None else None)

        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["USMSN"] * n, type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "plant_date": pa.array([None] * n, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            }
        )

    def report(self) -> None:
        for reason, n in sorted(self.dropped.items()):
            print(f"Madison: dropped {n:,} rows: {reason}", file=sys.stderr)
        sites = self.dropped["site with no tree (SPP_BOT)"]
        if self.seen and sites / self.seen > MAX_SITE_SHARE:
            raise RuntimeError(
                f"Madison: {sites:,} of {self.seen:,} rows are empty sites; "
                "SPP_BOT has changed meaning"
            )


def iter_row_chunks():
    """One ArcGIS page (2,000 features) at a time, geometry in WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


if __name__ == "__main__":
    check_site_ids()
    madison = Madison()
    table = stream_to_table(
        iter_row_chunks(), madison, keep=madison.keep, label="Madison OpenData"
    )
    madison.report()
    table = validate_coordinates(table, city="Madison", city_code="USMSN")
    table = enforce_tree_schema(table, city="Madison", data_source="MADISON_OPENDATA")
    emit(table)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Richmond, VA's public tree inventory, from the City's Urban Forestry division.

Licence: no licence stated; displayed with attribution only, not licensed for
reuse.  The ArcGIS item (902a0284181e4500aa220a6037d06d3d) has empty
`licenseInfo` and `accessInformation`, the City's Richmond GeoHub states only a
no-warranty disclaimer, and the dataset is not on data.richmondgov.com.

Source: "Tree_Inventory_Public_View" (ArcGIS Online item
902a0284181e4500aa220a6037d06d3d, owner `brad.duplantis@rva.gov_cor`, the
City of Richmond's organisation `cor`, snippet "Real-time Inventory of Trees
within the City of Richmond, Virginia"), a public view of Public Works /
Urban Forestry's live editing layer "Tree Inventory Live".  122,233 rows on
2026-11-19, stored in Virginia State Plane South feet (wkid 2284) and
reprojected to WGS84 by the server (`outSR=4326`).  It is the layer behind the
City's "Tree Inventory, City of Richmond, Virginia" web map and dashboard.

**A third of the layer is planting sites and stumps.**  Two coded columns say
so, and they mostly agree:

* `COND` (condition) holds `VSS`/`VSM`/`VSL`/`VSP` (Vacant Site Small/
  Medium/Large/Plantable, 42,984 rows) and `Stump` (2,267) besides the tree
  conditions `Good`, `Fair`, `Poor`, `NewPlant`, `Dead`, `WS` (wildlife snag)
  and `Excellent` (3 rows, not in the domain).
* `SPP` repeats it as a species code: `Vacant Site Small/Medium/Large/
  Plantable` and `Stump` -- which `is_not_a_tree` recognises.

A row either column calls a site or a stump is dropped (a handful disagree:
`Vacant Site Plantable` with `COND` = Fair and DBH 0, a `Ginkgo biloba` with
`COND` = Stump).  Dead trees and snags are trees and stay.  More than
`MAX_SITE_SHARE` of the layer dropped as sites raises, and so does a `COND`
value outside the known set on more than 1% of rows: either means the codes
changed meaning.  `Status` (In Service / Retired / Out of Service / Planned)
is `In Service` on every row the view publishes; anything else is dropped
with a count, in case the view stops filtering.  `SPP` = `Grass spp.` (1 row)
is not a tree and is dropped.

**The id is `GlobalID`** (`ric-{guid}`), non-null on every row.  `TreeID` is
the City's own number and is unique where present, but null on 302 rows.

**Species is `SPP`, a coded-value domain whose codes are scientific names**
(`Acer rubrum`, `Prunus spp.`, `Ulmus x`, `Malus x 'Prairifire'`); the shared
schema cuts `spp.`, bare hybrid marks and infraspecific ranks and reads the
quoted cultivars.  `SPECIES_OVERRIDES` reads the codes that are not a taxon as
written: cultivars written without quotes (`Prunus Kanzan`, `Nyssa sylvatica
Wildfire`, `Ulmus parvifolia Bosque`, `Cornus x Rutgan` -- a Stellar
dogwood, C. x rutgersensis), trade names (`Accolade Elm`,
`Princeton Sentry Ginkgo`, `Crimson Spire Columnar Oak`), the `Unknown
tree`/`Unknown shrub`/`Cactus sp.` sentinels, `Bamboo sp.` (a subfamily's
common name, published as Unknown) and `Quercus robur x bicolor` (Q. x warei,
which the shared rules would otherwise cut to Q. robur).  `Betula nigra
'Multi-stem'` and `'single_stem'` record a growth form, not a cultivar.  A
null `SPP` on a row whose `COND` is a tree condition (a tree was assessed)
publishes as Unknown.

`CULTIVAR` (a domain of 48 names, filled on ~2,700 rows: `October glory`,
`Natchez`, `Bracken`s Brown Beauty`) is the `cultivar` where `SPP` carries
none; `Multi-stem`/`Single-stem` (1,866 rows) are growth forms and dropped.

**DBH is `DBH`, inches** (median 8, 95th percentile 32, maximum 90 on tree
rows); zeros are nulled by the schema.  `TRUNKS` (stem count) has no canonical
column.  The layer records no planting date and no height or crown spread.
"""

import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    CACTUS_SPECIES,
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

LAYER = FeatureLayer(
    "https://services1.arcgis.com/k3vhq11XkBNeeOfM/arcgis/rest/services/"
    "Tree_Inventory_Public_View/FeatureServer/0",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,GlobalID,SPP,COND,Status,CULTIVAR,DBH"

# `COND` codes for a site or a stump (see the module docstring).
SITE_CONDITIONS = frozenset({"vss", "vsm", "vsl", "vsp", "stump"})
# `COND` codes for a standing tree; `excellent` is written on 3 rows though
# the domain does not list it.
TREE_CONDITIONS = frozenset({"good", "fair", "poor", "newplant", "dead", "ws", "excellent", ""})
IN_SERVICE = "in service"

# `SPP` codes that are not plants this map draws.
NOT_A_TREE_SPP = frozenset({"grass spp."})

# `SPP` codes that are not a taxon as written: (species, cultivar).
SPECIES_OVERRIDES: dict[str, tuple[str | None, str | None]] = {
    "unknown tree": (UNKNOWN_SPECIES, None),
    "unknown shrub": (SHRUB_SPECIES, None),
    "cactus sp.": (CACTUS_SPECIES, None),
    "bamboo sp.": (UNKNOWN_SPECIES, None),
    "prunus kanzan": ("Prunus serrulata", "Kanzan"),
    "nyssa sylvatica wildfire": ("Nyssa sylvatica", "Wildfire"),
    "cornus kousa scarlet fire": ("Cornus kousa", "Scarlet Fire"),
    "cornus x rutgan": ("Cornus x rutgersensis", "Rutgan"),
    "cornus x rutgan stellar pink": ("Cornus x rutgersensis", "Stellar Pink"),
    "ulmus parvifolia bosque": ("Ulmus parvifolia", "Bosque"),
    "ulmus x morton accolade": ("Ulmus", "Accolade"),
    "accolade elm": ("Ulmus", "Accolade"),
    "princeton sentry ginkgo": ("Ginkgo biloba", "Princeton Sentry"),
    "crimson spire columnar oak": ("Quercus x bimundorum", "Crimschmidt"),
    "fruitless sweetgum": ("Liquidambar styraciflua", None),
    "quercus robur x bicolor": ("Quercus x warei", None),
    "ilex x attenuata-fosteri": ("Ilex x attenuata", "Fosteri"),
    "betula nigra 'multi-stem'": ("Betula nigra", None),
    "betula nigra 'single_stem'": ("Betula nigra", None),
    "betula nigra 'single stem'": ("Betula nigra", None),
}

# `CULTIVAR` values that record a growth form, not a selection.
GROWTH_FORMS = frozenset({"multi-stem", "single-stem"})

# The 2026 layer is 37.6% sites and stumps; a jump means the codes changed.
MAX_SITE_SHARE = 0.55
# Share of rows with a `COND` outside the known set before the run refuses.
MAX_UNKNOWN_CONDITION_SHARE = 0.01


def code_key(value) -> str:
    """Lower-cased, whitespace-collapsed, ASCII-only (`Accolade® Elm` arrives
    mis-encoded as `AccoladeÂ® Elm`)."""
    text = " ".join(str(value or "").split()).lower()
    return " ".join(text.encode("ascii", "ignore").decode().split())


def tidy_cultivar(value) -> str | None:
    text = " ".join(str(value or "").replace("`", "'").split())
    if not text or text.lower() in GROWTH_FORMS:
        return None
    return " ".join(w[:1].upper() + w[1:] for w in text.split(" "))


class Richmond:
    def __init__(self) -> None:
        self.dropped: Counter[str] = Counter()
        self.unknown_conditions: Counter[str] = Counter()
        self.seen = 0

    def keep(self, feature: dict) -> bool:
        self.seen += 1
        rec = feature.get("attributes") or {}
        cond = code_key(rec.get("COND"))
        spp = rec.get("SPP")
        if code_key(rec.get("Status")) not in (IN_SERVICE, ""):
            self.dropped[f"Status = {rec.get('Status')}"] += 1
            return False
        if cond in SITE_CONDITIONS or is_not_a_tree(spp):
            self.dropped["vacant site or stump (COND or SPP)"] += 1
            return False
        if code_key(spp) in NOT_A_TREE_SPP:
            self.dropped["not a tree (SPP = Grass spp.)"] += 1
            return False
        if cond not in TREE_CONDITIONS:
            self.unknown_conditions[str(rec.get("COND"))] += 1
        if not str(rec.get("GlobalID") or "").strip("{} "):
            self.dropped["no GlobalID"] += 1
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
            tree_id.append(f"ric-{str(rec['GlobalID']).strip('{} ').lower()}")
            raw = rec.get("SPP")
            key = code_key(raw)
            if key in SPECIES_OVERRIDES:
                sp, cv = SPECIES_OVERRIDES[key]
            elif not key:
                sp, cv = UNKNOWN_SPECIES, None
            else:
                sp, cv = normalize_species(raw), extract_cultivar(raw)
            species.append(sp)
            cultivar.append(cv or tidy_cultivar(rec.get("CULTIVAR")))
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            raw_dbh = rec.get("DBH")
            dbh.append(float(raw_dbh) if raw_dbh is not None else None)

        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["USRIC"] * n, type=pa.string()),
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
            print(f"Richmond: dropped {n:,} rows: {reason}", file=sys.stderr)
        for cond, n in sorted(self.unknown_conditions.items()):
            print(f"Richmond: kept {n:,} rows with unlisted COND {cond!r}", file=sys.stderr)
        if not self.seen:
            return
        sites = self.dropped["vacant site or stump (COND or SPP)"]
        if sites / self.seen > MAX_SITE_SHARE:
            raise RuntimeError(
                f"Richmond: {sites:,} of {self.seen:,} rows are sites or stumps; "
                "COND/SPP have changed meaning"
            )
        unknown = sum(self.unknown_conditions.values())
        if unknown / self.seen > MAX_UNKNOWN_CONDITION_SHARE:
            raise RuntimeError(
                f"Richmond: {unknown:,} of {self.seen:,} rows carry a COND outside "
                f"the known codes ({dict(self.unknown_conditions)}); read the domain again"
            )


def iter_row_chunks():
    """One ArcGIS page (2,000 features) at a time, geometry in WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


if __name__ == "__main__":
    richmond = Richmond()
    table = stream_to_table(
        iter_row_chunks(), richmond, keep=richmond.keep, label="Richmond OpenData"
    )
    richmond.report()
    table = validate_coordinates(table, city="Richmond", city_code="USRIC")
    table = enforce_tree_schema(table, city="Richmond", data_source="RICHMOND_OPENDATA")
    emit(table)

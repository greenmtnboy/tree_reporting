#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Las Vegas, NV's municipal tree inventory, published by the City's Office of GIS.

Licence: no licence stated; displayed with attribution only, not licensed for
reuse.  The ArcGIS items (Park Trees, 05622dec3c454882abf26c4e0ec8370f, and the
older CLV_Trees copy, c4d5a7b766264b03940c6004ea2b6211, both owner
`OfficeOfGIS_LasVegas`) carry only a no-warranty disclaimer in `licenseInfo`
("this data is for reference only ... The City of Las Vegas makes no warranty,
representation or guarantee of any kind") and empty `accessInformation`, and
the City's open data portal (opendataportal-lasvegas.opendata.arcgis.com,
dataset `lasvegas::park-trees`) repeats that disclaimer; its "Terms of Use"
link is the Hub template's placeholder (`href="#"`).

Source: "Park Trees" (`Trees_PRD/FeatureServer/10`), the City of Las Vegas's
live tree layer (editor tracking on, `editingInfo` moving; the item's
description reads "Last updated: Nov 18 2026").  43,915 rows on 2026-11-20,
stored in Nevada State Plane East feet (wkid 102707/3421) and reprojected to
WGS84 by the server (`outSR=4326`).  Despite the name it is the whole City
inventory: parks (21,670 rows), golf courses, streetscapes, medians,
facilities, fire stations, schools and trails (`LOC_TYPE`).  The catalogue
lead, `CLV_Tree_Sites/FeatureServer/0` ("CLV_Trees"), is a frozen 2024 copy
of the same 43,764 rows without the 151 added since, and is not read.

**The id is `UNIQUEID`** (`las-{UNIQUEID}`), the inventory's own site number
(mostly `1`, `2`, `10`, ...; some field-collector keys such as `DROID
201702240902708520697` or `2081_177534452`): 43,764 distinct values, never
repeated.
It is the stable key, not `OBJECTID`: the same site carries OBJECTID 18,493
here and FID 18,972 in the 2024 copy (UNIQUEID 20000 in both, same point), so
the layer has been reloaded and OBJECTID renumbered (1..43,915, dense).  `ID`
is a different number (`19999` for UNIQUEID 20000) and is not used.  The 151
features digitised in December 2025 (Cragin Park; no UNIQUEID, `ID` = -1, no
STATUS, DBH or condition) have no id the City will keep, and are dropped with
a count until the City numbers them.

**`STATUS` says what a row is**: `Tree` (33,597), `Palm` (3,466),
`Other/Unknown` (4,151), `Vacant Site` (2,302) and `Stump` (248).  Sites and
stumps are dropped.  `Other/Unknown` rows are plants the survey did not
identify (4,126 `SPP_BOT` = `Unknown`, banded 0-3 in DBH and 1-15 ft tall,
mostly along the Summerlin Parkway and Alta medians) and stay as `Unknown`,
as every city keeps an unnamed tree -- except the inventory's street furniture
under the same status (`Bench`, 19; `Fertilizer Injector`, 4), which is
dropped.  A `SPP_BOT` site marker (`vacant site medium`, `stump`) under a
`Tree` status is dropped by `is_not_a_tree`.  Dead trees (`COND` = Dead) and
trees marked for removal (`REMOVEREAS`, `MAINT` = Remove) are standing trees
and stay.  An unlisted STATUS on more than 1% of rows refuses the build.

**Species is `SPP_BOT`, a free-text scientific name** (`SPP_COM` is the
common name and is not read).  The shared rules cut `var.`/`ssp.` and read the
quoted cultivars (`Parkinsonia x 'Desert Museum'`).  `SOURCE_SPELLINGS`
handles what is particular to this inventory: a hybrid sign written on a
plain species (`Fraxinus x velutina`, 1,941 rows; `Prosopis x chilensis`,
`Prosopis x alba`, `Quercus x virginiana`), which publishes as the species;
`Pistacia X atlantica` (1,169), a hybrid with P. atlantica as one parent
(most likely the trade's 'Red Push', P. atlantica x P. integerrima, which the
2025 rows write `Pistacia red Push`, but the source does not say so),
published at genus; the oak hybrid formulas
(`Quercus macrocarpa x gambelii`), published at genus because the shared rule
would cut them to the first parent; curly-quoted cultivars; and the
misspellings in the rows added in 2025 (`Aracia Stenophylla`, `Havarda
Mexicana`).  Misspellings proper (`Ulmus amaericana`, `Casuarinia
cunninghamiana`) are the shared maps' job.

**`DBH` is a published band in inches**, not a measurement: `0-3`, `4-6`,
`7-12`, `13-18`, `19-24`, `25-30`, `31-36`, `>36`, or `---` (not measured,
2,069 rows, published null).  The bands are the standard inch classes (palms
fall in 7-24, a 0-3 tree is 1-15 ft tall).  Each band is read as its midpoint,
the open-topped `>36` as its bound plus half the common 6-inch width (39 in),
Denver's rule.  An unknown band label on more than 1% of rows refuses the
build.  `WIDTH` and `HEIGHT` are bands in feet; `WIDTH` is not published as
`crown_width_m` because its `46-60` band is incoherent (434 trees banded 0-3 in
DBH carry a 46-60 ft crown, almost all from one survey contractor), and height
has no canonical column.  `INV_DATE` is the survey date (2009-2022), not a
planting, so `plant_date` is null.
"""

import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
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
    "https://services1.arcgis.com/F1v0ufATbBQScMtY/arcgis/rest/services/"
    "Trees_PRD/FeatureServer/10",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,UNIQUEID,STATUS,SPP_BOT,DBH"

ID_PREFIX = "las"

# `STATUS` values.  Sites and stumps are dropped; the rest are plants.
SITE_STATUSES = frozenset({"vacant site", "stump"})
PLANT_STATUSES = frozenset({"tree", "palm", "other/unknown"})
MAX_UNKNOWN_STATUS_SHARE = 0.01
# The 2026 layer is 5.8% sites and stumps; a jump means STATUS changed meaning.
MAX_SITE_SHARE = 0.25

# `SPP_BOT` values that are street furniture, not plants.
NOT_A_PLANT = frozenset({"bench", "fertilizer injector"})

# `SPP_BOT` values that are not a taxon as the shared rules would read them:
# exact source value (whitespace collapsed, lower-cased) -> (species, cultivar).
SOURCE_SPELLINGS: dict[str, tuple[str, str | None]] = {
    "fraxinus x velutina": ("Fraxinus velutina", None),
    "prosopis x chilensis": ("Prosopis chilensis", None),
    "prosopis x alba": ("Prosopis alba", None),
    "quercus x virginiana": ("Quercus virginiana", None),
    "pistacia x atlantica": ("Pistacia", None),
    "pistacia red push": ("Pistacia", "Red Push"),
    "quercus macrocarpa x gambelii": ("Quercus", None),
    "quercus gambelii x macrocarpa": ("Quercus", None),
    "robinia psuedoacacia 'purple robe'": ("Robinia pseudoacacia", "Purple Robe"),
    "aracia stenophylla": ("Acacia stenophylla", None),
    "havarda mexicana": ("Havardia mexicana", None),
    "brachychiton populneus tree": ("Brachychiton populneus", None),
}

# DBH bands, inches -> midpoint.  `>36` is open-topped: its bound plus half the
# common 6-inch width.
DBH_BAND_IN: dict[str, float] = {
    "0-3": 1.5,
    "4-6": 5.0,
    "7-12": 9.5,
    "13-18": 15.5,
    "19-24": 21.5,
    "25-30": 27.5,
    "31-36": 33.5,
    ">36": 39.0,
}
DBH_NOT_MEASURED = frozenset({"", "---"})
MAX_BAND_PARSE_FAILURES = 0.01


def code_key(value) -> str:
    """Lower-cased, whitespace-collapsed, curly quotes straightened."""
    text = str(value or "").replace("‘", "'").replace("’", "'")
    return " ".join(text.split()).lower()


def species_of(raw) -> tuple[str | None, str | None]:
    """(species, cultivar) for one `SPP_BOT` value."""
    key = code_key(raw)
    if key in SOURCE_SPELLINGS:
        return SOURCE_SPELLINGS[key]
    if not key or key == "unknown":
        return UNKNOWN_SPECIES, None
    text = " ".join(str(raw).replace("‘", "'").replace("’", "'").split())
    return normalize_species(text), extract_cultivar(text)


class LasVegas:
    def __init__(self) -> None:
        self.seen = 0
        self.dropped: Counter[str] = Counter()
        self.unknown_statuses: Counter[str] = Counter()
        self.dbh_read = 0
        self.dbh_failures: Counter[str] = Counter()

    def keep(self, feature: dict) -> bool:
        self.seen += 1
        rec = feature.get("attributes") or {}
        status = code_key(rec.get("STATUS"))
        spp = rec.get("SPP_BOT")
        if not str(rec.get("UNIQUEID") or "").strip():
            self.dropped["no UNIQUEID (added since the last numbering)"] += 1
            return False
        if status in SITE_STATUSES:
            self.dropped[f"STATUS = {rec.get('STATUS')}"] += 1
            return False
        if is_not_a_tree(spp):
            self.dropped["SPP_BOT names a site or stump"] += 1
            return False
        if code_key(spp) in NOT_A_PLANT:
            self.dropped[f"not a plant (SPP_BOT = {spp})"] += 1
            return False
        if status not in PLANT_STATUSES:
            self.unknown_statuses[str(rec.get("STATUS"))] += 1
        return True

    def dbh(self, value) -> float | None:
        self.dbh_read += 1
        text = "".join(str(value or "").split())
        if text in DBH_NOT_MEASURED:
            return None
        mid = DBH_BAND_IN.get(text)
        if mid is None:
            self.dbh_failures[text] += 1
        return mid

    def __call__(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            tree_id.append(f"{ID_PREFIX}-{str(rec['UNIQUEID']).strip()}")
            sp, cv = species_of(rec.get("SPP_BOT"))
            species.append(sp)
            cultivar.append(cv)
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            dbh.append(self.dbh(rec.get("DBH")))

        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["USLAS"] * n, type=pa.string()),
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
            print(f"Las Vegas: dropped {n:,} rows: {reason}", file=sys.stderr)
        for status, n in sorted(self.unknown_statuses.items()):
            print(f"Las Vegas: kept {n:,} rows with unlisted STATUS {status!r}", file=sys.stderr)
        if not self.seen:
            return
        sites = sum(n for reason, n in self.dropped.items() if reason.startswith("STATUS"))
        if sites / self.seen > MAX_SITE_SHARE:
            raise RuntimeError(
                f"Las Vegas: {sites:,} of {self.seen:,} rows are sites or stumps; "
                "STATUS has changed meaning"
            )
        unknown = sum(self.unknown_statuses.values())
        if unknown / self.seen > MAX_UNKNOWN_STATUS_SHARE:
            raise RuntimeError(
                f"Las Vegas: {unknown:,} of {self.seen:,} rows carry a STATUS outside "
                f"the known values ({dict(self.unknown_statuses)}); read the layer again"
            )
        bad = sum(self.dbh_failures.values())
        if bad:
            share = bad / max(self.dbh_read, 1)
            print(
                f"Las Vegas: {bad:,} unreadable DBH bands ({share:.2%}): "
                f"{self.dbh_failures.most_common(10)}",
                file=sys.stderr,
            )
            if share > MAX_BAND_PARSE_FAILURES:
                raise RuntimeError(
                    f"Las Vegas DBH: {share:.2%} of rows carry a band label this "
                    "ingest does not know; the portal has changed its bands"
                )


def iter_row_chunks():
    """One ArcGIS page (2,000 features) at a time, geometry in WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


if __name__ == "__main__":
    las_vegas = LasVegas()
    table = stream_to_table(
        iter_row_chunks(), las_vegas, keep=las_vegas.keep, label="Las Vegas OpenData"
    )
    las_vegas.report()
    table = validate_coordinates(table, city="Las Vegas", city_code="USLAS")
    table = enforce_tree_schema(table, city="Las Vegas", data_source="LAS_VEGAS_OPENDATA")
    emit(table)

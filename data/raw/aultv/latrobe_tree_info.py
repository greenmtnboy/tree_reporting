#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Latrobe City, VIC's municipal tree inventory.

Source: "Tree Inventory View" (item f41d6a33a5a545e9b84aca02d8448377, owner
gis_latrobecc, the Latrobe City Council's own ArcGIS Online organisation,
latrobecc.maps.arcgis.com): "The latest Tree Inventory dataset for Latrobe
... a view layer showing all Tree Inventory in Latrobe", the hosted layer
`Tree_Inventory_All View` (the space is in the service name), 71,567 rows on
2026-11-22, edited live.  Licence: the item's licence field, "Creative
Commons Attribution 4.0 (CC-BY) License".  The organisation also publishes
four views cut from the same table by status (Trees only, Removed, Vacant,
Stumps); the All view is read and cut here, so a status the council adds
later refuses the build instead of vanishing.

**Not every row is a tree in the ground.**  `status` holds `Tree` (63,397),
`Removed` (4,965, every one with a `removed_date`), `Vacant Tree Location`
(3,086) and `Stump` (119).  Only `Tree` is kept; an unseen status refuses
the build.  Two more kinds of row are not one tree and are dropped with a
count: `Native Roadside Vegetation` / `Rural Roadside - All Tree Species`
(a stretch of roadside bush recorded as one point) and `other` / `shrub or
creeper`.  Private trees the council records (`private_tree`, 856) are kept.

**The id is `id`**, the council's asset-system record number (3,729,651 ..
7,510,480, not dense), non-null and distinct over the whole layer
(a `groupBy id having count > 1` query returns nothing).  `asset_id`
(`ST_1`) is null on 49,429 rows and repeated on two values, and there is no
GlobalID; `OBJECTID` is a dense 1..N.  Prefixed `ltv-`.

**Species is `genus` + `species`**, the epithet column written loosely: a
cultivar quoted in it (`grandiflora 'Little Gem'`, backticks and double
quotes too) or written after the epithet in capitals (`mannifera Little
Spotty`, `rubrum October Glory`); a bare `cv` / `cultivar` / `sp.`; ranks
(`ssp.`, `v.`, `var.`, `f.`) and unranked trinomials (`leucoxylon
megalocarpa`), which publish as the binomial; the genus abbreviated in front
of the epithet (`A. × freemanii`) or repeated (`Salix chilensis`); and
hybrid formulae (`populneus x acerifolious`, `indica x L. fauriei
varieties`), which leave the genus.  A whole name in `genus` (`Pyrus
calleryana 'Bradford'` with `Pear Tree` as the epithet) is read from
`genus`.  `SOURCE_NAMES` holds the cultivars written as the epithet
(`Prunus Kanzan`, `Acer Autumn Blaze`).  `variety` carries the cultivar on
about 10% of rows (`Chanticleer`, `Mt Fuji`, `Greenspire`); the few values
that are not a cultivar (`Standard Box`, `Bottletree`, `CV`) are ignored,
and `x lavalleei` under a bare `Crataegus` is the hybrid epithet.
`Not specified`, `Unknown Species`, `mixed natives` and the remaining
`other` rows (`dead tree`, `unidentified`, `not on list`) are the Unknown
sentinel.  `common_name` is not mapped.  Misspellings (`Ginko`, `palmatun`,
`Darcrycarpus`) are left for the shared misspelling map.  More than 1% of
named rows with an unreadable epithet refuses the build.

**`dbh` is centimetres of trunk diameter**, a whole number: the layer's own
tree protection zone is 12 x dbh (`dbh` 40 -> `tpz` 4.8 m, the AS 4970
rule), and the values sit inside their `dbh_range` band.  0 (12,950 trees)
is unmeasured and becomes null.  `dbh_range` and `circumference` are bands
(the latter in mm) and are not used.  **`canopy_m` is the crown spread in
whole metres** (median 4); 0 and the handful of impossible values (330,
580) fall to the shared crown guard.  `height_m` has no canonical column.

**`planted_date`** is text, `YYYY-MM-DD hh:mm:ss`, on about 3,000 trees,
nearly all 2022-2026 plantings; the date part is read as the council's local
date, and a value that does not parse counts against a 1% refusal.

Geometry is stored in GDA2020 / MGA zone 55 and requested as WGS84
(`outSR=4326`).
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
    normalize_species,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services-ap1.arcgis.com/AtixmNNZDz8cwc9l/arcgis/rest/services/"
    "Tree_Inventory_All%20View/FeatureServer/0",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,id,status,genus,species,variety,dbh,canopy_m,planted_date"

CITY_CODE = "AULTV"
ID_PREFIX = "ltv"
CM_PER_INCH = 2.54
MAX_UNREADABLE_SHARE = 0.01

TREE_STATUS = "tree"
NOT_A_TREE_STATUS = frozenset({"removed", "vacant tree location", "stump"})

# (genus, epithet) rows, lower-cased, that are not one tree.
NOT_A_TREE_NAMES = frozenset(
    {
        ("native roadside vegetation", "rural roadside - all tree species"),
        ("other", "shrub or creeper"),
    }
)
# `genus` values that say the tree was not named.
UNRECORDED_GENUS = frozenset({"not specified", "unknown species", "unknown", "other", "mixed"})
# First epithet tokens that say the species was not named.
UNRECORDED_EPITHET = frozenset({"sp.", "sp", "spp.", "spp", "cv", "cv.", "cultivar"})
# Tokens after the epithet that only say "some cultivar".
CULTIVAR_MARKERS = frozenset({"cv", "cv.", "cultivar", "cultivars", "varieties"})
RANKS = frozenset({"ssp.", "ssp", "subsp.", "subsp", "var.", "var", "v.", "f.", "forma"})

# (genus, epithet) as written, after quote and whitespace cleaning ->
# (scientific name, cultivar).  Cultivars written in the epithet column.
SOURCE_NAMES: dict[tuple[str, str], tuple[str, str | None]] = {
    ("Prunus", "Kanzan"): ("Prunus serrulata", "Kanzan"),
    # Autumn Blaze is the trade name of Acer x freemanii 'Jeffersred'.
    ("Acer", "Autumn Blaze"): ("Acer x freemanii", "Jeffersred"),
    ("Lagerstroemia", "Natchez"): ("Lagerstroemia", "Natchez"),
    ("Callistemon", "Endeavour"): ("Callistemon", "Endeavour"),
    ("Callistemon", "Harkness"): ("Callistemon", "Harkness"),
    # Raywood is the claret ash, Fraxinus angustifolia (Shepparton's rule).
    ("Fraxinus", "Raywood"): ("Fraxinus angustifolia", "Raywood"),
    ("Fraxinus", "'Raywood'"): ("Fraxinus angustifolia", "Raywood"),
}

# `variety` values that are not a cultivar (a nursery form, a common name,
# the genus again, a note) -> ignored.
VARIETY_NOT_A_CULTIVAR = frozenset(
    {
        "standard lilly pilly", "standard box", "bottletree", "cercis", "cv",
        "rural roadside - all tree species",
    }
)
# `variety` values spelled other than the registered cultivar name.
VARIETY_NAMES = {
    "'jeffersred' autumn blaze family": "Jeffersred",
    "varigata": "Variegata",
}

QUOTES = str.maketrans({"‘": "'", "’": "'", "`": "'", '"': "'", "“": "'", "”": "'", " ": " ", "×": "x"})
QUOTED = re.compile(r"'([^']*)'?")
PARENTHETICAL = re.compile(r"\s*\([^)]*\)")
GENUS_INITIAL = re.compile(r"^[A-Z]\.$")
EPITHET = re.compile(r"^[a-z][a-z-]*$")
PLANTED = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T]\d{2}:\d{2}:\d{2})?$")


def clean_text(value) -> str:
    if value is None:
        return ""
    return " ".join(PARENTHETICAL.sub("", str(value).translate(QUOTES)).split())


def tidy_cultivar(value: str | None) -> str | None:
    value = clean_text(value).strip("'").strip()
    if not value:
        return None
    return " ".join(w[:1].upper() + w[1:] for w in value.split(" "))


class Latrobe:
    def __init__(self) -> None:
        self.dropped: Counter[str] = Counter()
        self.unreadable: Counter[str] = Counter()
        self.named = 0
        self.dated = 0
        self.bad_dates: Counter[str] = Counter()

    # -- filtering ---------------------------------------------------------
    def keep(self, feature: dict) -> bool:
        rec = feature.get("attributes") or {}
        status = clean_text(rec.get("status")).lower()
        if status in NOT_A_TREE_STATUS:
            self.dropped[f"status {status!r}"] += 1
            return False
        if status != TREE_STATUS:
            raise RuntimeError(f"Latrobe City: unseen status value {rec.get('status')!r}")
        pair = (clean_text(rec.get("genus")).lower(), clean_text(rec.get("species")).lower())
        if pair in NOT_A_TREE_NAMES:
            self.dropped[f"not one tree {pair[0]} / {pair[1]}"] += 1
            return False
        if rec.get("id") is None:
            self.dropped["no id"] += 1
            return False
        return True

    # -- species -----------------------------------------------------------
    def name_and_cultivar(self, genus_raw, epithet_raw, variety_raw) -> tuple[str | None, str | None]:
        genus = clean_text(genus_raw)
        epithet = clean_text(epithet_raw)
        if not genus:
            return None, None
        self.named += 1
        if genus.lower() in UNRECORDED_GENUS:
            return UNKNOWN_SPECIES, None
        override = SOURCE_NAMES.get((genus, epithet))
        if override:
            return normalize_species(override[0]), override[1]

        # A whole name in `genus`: the epithet column then holds a common
        # name (`Pear Tree`) or the epithet again.
        if " " in genus:
            genus, _, epithet = genus.partition(" ")

        cultivar = None
        match = QUOTED.search(epithet)
        if match:
            cultivar = tidy_cultivar(match.group(1))
            epithet = epithet[: match.start()].strip()

        tokens = epithet.split()
        # The genus repeated (`Salix chilensis`) or abbreviated (`A. x freemanii`).
        if tokens and tokens[0].lower() == genus.lower():
            tokens = tokens[1:]
        if tokens and GENUS_INITIAL.match(tokens[0]):
            tokens = tokens[1:]

        variety = clean_text(variety_raw)
        if not tokens and variety.lower().startswith("x "):
            # `Crataegus` with `x lavalleei` as the variety.
            tokens, variety = variety.split(), ""
        cultivar = cultivar or self.variety_cultivar(variety)

        parts = [genus]
        if tokens and tokens[0].lower() == "x":
            parts.append("x")
            tokens = tokens[1:]
        if not tokens or tokens[0].lower() in UNRECORDED_EPITHET:
            return normalize_species(genus), cultivar
        first, rest = tokens[0].lower(), tokens[1:]
        if not EPITHET.match(first):
            self.unreadable[f"{genus} {epithet}"] += 1
            return normalize_species(genus), cultivar
        # A hybrid formula between two species names no one taxon.
        if any(t.lower() == "x" for t in rest):
            return normalize_species(genus), cultivar
        parts.append(first)

        # After the epithet: a rank or an unranked infraspecific epithet
        # (both dropped), a cultivar marker, or a cultivar in capitals.
        if rest and rest[0].lower() not in RANKS and rest[0].lower() not in CULTIVAR_MARKERS:
            if rest[0][:1].isupper():
                cultivar = cultivar or tidy_cultivar(" ".join(rest))
            elif not EPITHET.match(rest[0]):
                self.unreadable[f"{genus} {epithet}"] += 1
        return normalize_species(" ".join(parts)), cultivar

    @staticmethod
    def variety_cultivar(variety: str) -> str | None:
        if not variety or variety.lower() in VARIETY_NOT_A_CULTIVAR:
            return None
        named = VARIETY_NAMES.get(variety.lower())
        return named or tidy_cultivar(variety)

    # -- dates -------------------------------------------------------------
    def planted(self, value) -> date | None:
        text = clean_text(value)
        if not text:
            return None
        self.dated += 1
        match = PLANTED.match(text)
        if match:
            try:
                return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                pass
        self.bad_dates[text] += 1
        return None

    # -- rows --------------------------------------------------------------
    def transform(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        plant_date: list[date | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []
        crown: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            tree_id.append(f"{ID_PREFIX}-{int(rec['id'])}")
            name, cv = self.name_and_cultivar(rec.get("genus"), rec.get("species"), rec.get("variety"))
            species.append(name)
            cultivar.append(cv if name and name != UNKNOWN_SPECIES else None)
            plant_date.append(self.planted(rec.get("planted_date")))
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            cm = rec.get("dbh")
            dbh.append(float(cm) / CM_PER_INCH if cm is not None and cm > 0 else None)
            width = rec.get("canopy_m")
            crown.append(float(width) if width is not None and width > 0 else None)

        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array([CITY_CODE] * n, type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "plant_date": pa.array(plant_date, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
                "crown_width_m": pa.array(crown, type=pa.float64()),
            }
        )

    def report(self) -> None:
        print(
            "Latrobe City: dropped "
            + ", ".join(f"{v} {k}" for k, v in sorted(self.dropped.items())),
            file=sys.stderr,
        )
        bad = sum(self.unreadable.values())
        if bad:
            print(
                f"Latrobe City: {bad} row(s) with an unreadable epithet (published as the genus): "
                + ", ".join(f"{k!r} x{v}" for k, v in self.unreadable.most_common(20)),
                file=sys.stderr,
            )
        if self.named and bad / self.named > MAX_UNREADABLE_SHARE:
            raise RuntimeError(
                f"Latrobe City: {bad} of {self.named} species values unreadable "
                f"(> {MAX_UNREADABLE_SHARE:.0%}); has the column changed shape?"
            )
        bad_dates = sum(self.bad_dates.values())
        if bad_dates:
            print(
                f"Latrobe City: {bad_dates} unreadable planted_date value(s): "
                f"{self.bad_dates.most_common(10)}",
                file=sys.stderr,
            )
        if self.dated and bad_dates / self.dated > MAX_UNREADABLE_SHARE:
            raise RuntimeError(
                f"Latrobe City: {bad_dates} of {self.dated} planted_date values unreadable "
                f"(> {MAX_UNREADABLE_SHARE:.0%}); has the column changed shape?"
            )


if __name__ == "__main__":
    city = Latrobe()
    pages = iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)
    table = stream_to_table(pages, city.transform, keep=city.keep, label="Latrobe City OpenData")
    city.report()
    table = validate_coordinates(table, city="Latrobe City", city_code=CITY_CODE)
    table = enforce_tree_schema(table, city="Latrobe City", data_source="LATROBE_OPENDATA")
    emit(table)

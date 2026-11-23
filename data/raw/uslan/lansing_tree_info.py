#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Lansing, Michigan's City tree inventory.

No licence stated; displayed with attribution only, not licensed for reuse.

Source: "Trees" (ArcGIS item 156f8898d26a471dae7c858e46851b29, owner
`JvolkCOL`, tagged "City of Lansing", in the City of Lansing's ArcGIS Online
organisation `pNPbgWy7hpfFGWoZ`, the one behind data-lansing.opendata.arcgis.com
and lansing.maps.arcgis.com), layer
`services1.arcgis.com/pNPbgWy7hpfFGWoZ/arcgis/rest/services/Trees/FeatureServer/1`:
67,102 rows on 2026-11-22, the street and park trees the City's Cityworks
asset system tracks.  Stored in Michigan State Plane South (EPSG:6499) and
read reprojected to WGS84 (`outSR=4326`).  `Lansing_Trees_Local` (item
d2dad38a252947528b3ac36f7fde9ca3, 67,094 rows, last edited 2023) is an older
copy of the same table and is not read.

Licence: the item's licence and access fields are empty, and the item is not
in the "Lansing Open Data" group the City's open-data Hub publishes (the Hub
site's own CC-BY-SA licence covers that group's nine layers, not this one).
https://www.arcgis.com/home/item.html?id=156f8898d26a471dae7c858e46851b29
Attribute to the City of Lansing.

**Which rows are trees.**  `SPP` is the inventory's working species field and
the one the City overwrites when a site changes: a removed tree keeps its old
`GENUS`/`SPECIES` but its `SPP` becomes `Stump` or `Vacant Site`.  Rows whose
`SPP` is `Vacant Site`, `Stump`, `Refused tree` (a resident refused a
planting) or `No future site` are dropped.

`GIS_STATUS` (a coded-value domain: Found, Missing, Uninspected) is the
2015-16 GIS audit of the 1990s-2000s inventory records, and only `Found` and
the unaudited rows added since (null) are read:

* `Missing` (33,251 rows; 13,604 with a species) is a record the audit could
  not place: its notes are "Vacant", "Stump", "Duplicate?", "no such address",
  "Where is this tree??" by the thousand, and 6,971 of them sit stacked at
  site centroids (628 at the 119th Armory, 265 at Davis Park).  Of the 2025-26
  CSO 015S project's trim/removal tags, standing trees surveyed afresh, ~94%
  are `Found` rows, against 70% of the layer.
* `Uninspected` (499) are plantings pinned to a park or cemetery centroid
  (121 at one point, 483 of them stacked): real trees at no real position.

**The id is `CityworksID`** (`lansing-{CityworksID}`), the Cityworks asset id
(1..70693): non-null on all but one row, and stable -- 66,980 of the 2023
copy's rows carry the same id at the same position today.  `OBJECTID` runs
1..71211 with gaps (an edited table, not a reload).  `FACILITYID` is null on
484 rows and mixes two formats.  A handful of recent rows repeat a
`CityworksID` (8 values in Nov 2026); those take
`lansing-{CityworksID}-{OBJECTID}` (Baltimore's rule), and a row with none is
dropped with a logged count.

**Species is `GENUS` + `SPECIES`**, both coded-value domains whose codes are
the botanical names (`Acer` + `platanoides 'Crimson King'`); `SPP` is the
same name abbreviated by hand (`Acer plat. 'Crimson King'`) and is not read
for the name.  `spp.` is the genus alone; a quoted selection becomes
`cultivar`.  `SPECIES_FIXES` reads the domain codes the general rule cannot
(bare cultivars written as an epithet, `celebration`; hybrid marks), and
`GENUS_FIXES` the two misspelt genus codes.  Single misspelt epithets
(`glyptostrobiodes`) are left to the shared misspelling map.  A parse-failure
guard refuses more than 1% unreadable values.

**DBH is `DBH`, inches** (median ~9, mostly 3-inch classes; `DIAMETER`
repeats it).  `0` is the unmeasured placeholder and is nulled, and so is
`150`, stamped only on Unknown/Vacant/Stump rows.  `HEIGHT` is a 10-foot band
string and has no canonical column; there is no crown spread.

**Planting date** is `INSTALLDATE` (93 rows, nearly all 2016-2023
plantings).  `INV_DATE` is the inventory visit, not a planting, and is not
read.
"""

import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_date, esri_point, iter_features
from shared.ingest import (
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services1.arcgis.com/pNPbgWy7hpfFGWoZ/arcgis/rest/services/Trees/FeatureServer/1",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,CityworksID,GENUS,SPECIES,SPP,DBH,INSTALLDATE,GIS_STATUS"
ID_PREFIX = "lansing"

# `SPP` values (lower-cased) that record a site with no standing tree.
NOT_A_TREE_SPP = frozenset({"vacant site", "stump", "refused tree", "no future site"})

# `GIS_STATUS` values read (None: added since the audit); see the docstring.
KEPT_STATUSES = frozenset({"Found", None})
DROPPED_STATUSES = frozenset({"Missing", "Uninspected"})

# DBH values that are placeholders rather than measurements.
PLACEHOLDER_DBH = frozenset({0.0, 150.0})

# Refuse to publish when more than this share of named rows cannot be read.
MAX_UNPARSED_SHARE = 0.01

GENUS_FIXES: dict[str, str] = {
    "cladrastris": "Cladrastis",
    "euonymous": "Euonymus",
}

# (genus, SPECIES code) lower-cased -> `Taxon 'Cultivar'` (or the taxon).
SPECIES_FIXES: dict[tuple[str, str], str] = {
    # Freeman maple selections, written as a bare cultivar or `f.`
    ("acer", "celebration"): "Acer x freemanii 'Celebration'",
    ("acer", "f. 'sienna glen'"): "Acer x freemanii 'Sienna Glen'",
    # Shantung x Norway maple ('Norwegian Sunset', 'Pacific Sunset').
    ("acer", "truncatum x platanoides"): "Acer",
    # The layer's SPP reads "Acer freemanii" on this row: a Freeman maple
    # filed under the ash genus.
    ("fraxinus", "freemanii"): "Acer x freemanii",
    ("aesculus", "carnea 'ft. mcnair'"): "Aesculus x carnea 'Fort McNair'",
    ("malus", "doubloons"): "Malus 'Doubloons'",
    ("malus", "thunderchild"): "Malus 'Thunderchild'",
    ("euonymus", "alata"): "Euonymus alatus",
}

# Cultivar spellings in the SPECIES domain (lower-cased) -> the cultivar.
CULTIVAR_FIXES: dict[str, str] = {
    "prarie pride": "Prairie Pride",
    "prariefire": "Prairifire",
    "fastigata": "Fastigiata",
    "green mt.": "Green Mountain",
    "columnnaire": "Columnaire",
    "roseo marginata": "Roseomarginata",
    "hessi": "Hessei",
}

PLACEHOLDER_EPITHETS = frozenset({"spp.", "spp", "sp.", "sp"})
UNKNOWN = frozenset({"unknown", "other"})


def clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).replace("‘", "'").replace("’", "'")
    text = " ".join(text.split())
    return text or None


def _cultivar(text: str) -> str | None:
    text = " ".join(text.strip().strip("'\"").split())
    if not text:
        return None
    return CULTIVAR_FIXES.get(text.lower(), text)


def _split(fixed: str) -> tuple[str, str | None]:
    taxon, quote, cultivar = fixed.partition(" '")
    return taxon, (cultivar.rstrip("'") if quote else None)


def parse_species(raw_genus, raw_species) -> tuple[str | None, str | None, bool]:
    """(taxon, cultivar, whether the value failed to parse).

    Examples:
        ("Acer", "platanoides 'Crimson King'")  -> ("Acer platanoides", "Crimson King")
        ("Malus", "spp. 'Red Baron'")           -> ("Malus", "Red Baron")
        ("Cornus", "x 'Celestial'")             -> ("Cornus", "Celestial")
        ("Laburnum", "x watereri 'Vossii'")     -> ("Laburnum x watereri", "Vossii")
        ("Taxus", " ")                          -> ("Taxus", None)
        (" ", " ")                              -> (None, None)
    """
    genus_text = clean(raw_genus)
    if genus_text is None:
        return None, None, False
    if genus_text.lower() in UNKNOWN:
        return "Unknown", None, False
    if not genus_text.isalpha():
        return None, None, True
    genus = GENUS_FIXES.get(genus_text.lower(), genus_text[:1].upper() + genus_text[1:].lower())
    text = clean(raw_species)
    if text is None or text.lower() in UNKNOWN:
        return genus, None, False
    fixed = SPECIES_FIXES.get((genus.lower(), text.lower()))
    if fixed is not None:
        return (*_split(fixed), False)

    head, quote, tail = text.partition("'")
    cultivar = _cultivar(tail) if quote else None
    words = head.split()
    if words and words[0].lower() in PLACEHOLDER_EPITHETS:
        words = words[1:]
    if words and words[0].lower() in ("x", "×") and len(words) == 1:
        # `x 'Celestial'`: a hybrid of the genus known only by its cultivar.
        words = []
    if not all(w.replace("-", "").isalpha() for w in words) or len(words) > 2:
        return None, None, True
    if words and words[0].lower() in ("x", "×"):
        words = ["x", words[1].lower()]
    else:
        words = [w.lower() for w in words]
    return " ".join([genus, *words]), cultivar, False


class Counts:
    not_a_tree: Counter = Counter()
    status: Counter = Counter()
    no_id = 0
    no_geometry = 0
    repeated_id_rows = 0
    unparsed: Counter = Counter()
    named = 0
    placeholder_dbh = 0


COUNTS = Counts()


def repeated_ids() -> set[str]:
    """CityworksIDs on more than one row, over the whole layer."""
    seen: Counter = Counter()
    for page in iter_features(LAYER, out_fields="OBJECTID,CityworksID", return_geometry=False):
        for feature in page:
            cw = clean((feature.get("attributes") or {}).get("CityworksID"))
            if cw:
                seen[cw] += 1
    if not seen:
        raise RuntimeError(f"{LAYER.base} returned no CityworksID values")
    return {cw for cw, n in seen.items() if n > 1}


def keep(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    spp = (clean(attrs.get("SPP")) or "").lower()
    if spp in NOT_A_TREE_SPP:
        COUNTS.not_a_tree[spp] += 1
        return False
    status = clean(attrs.get("GIS_STATUS"))
    if status not in KEPT_STATUSES:
        if status not in DROPPED_STATUSES:
            raise RuntimeError(
                f"Lansing ingest: unseen GIS_STATUS {status!r}; decide whether "
                "its rows are placed trees"
            )
        COUNTS.status[status] += 1
        return False
    if not clean(attrs.get("CityworksID")):
        COUNTS.no_id += 1
        return False
    lat, lon = esri_point(feature.get("geometry"))
    if lat is None or lon is None:
        COUNTS.no_geometry += 1
        return False
    return True


def dbh_inches(raw) -> float | None:
    if raw is None:
        return None
    value = float(raw)
    if value in PLACEHOLDER_DBH:
        COUNTS.placeholder_dbh += 1
        return None
    return value if value > 0 else None


def make_transform(repeated: set[str]):
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
            cw = clean(attrs["CityworksID"])
            if cw in repeated:
                COUNTS.repeated_id_rows += 1
                tree_id.append(f"{ID_PREFIX}-{cw}-{attrs['OBJECTID']}")
            else:
                tree_id.append(f"{ID_PREFIX}-{cw}")
            name, cv, failed = parse_species(attrs.get("GENUS"), attrs.get("SPECIES"))
            if clean(attrs.get("GENUS")):
                COUNTS.named += 1
            if failed:
                COUNTS.unparsed[(attrs.get("GENUS"), attrs.get("SPECIES"))] += 1
            species.append(name)
            cultivar.append(cv)
            planted.append(esri_ms_to_date(attrs.get("INSTALLDATE")))
            latitude.append(lat)
            longitude.append(lon)
            dbh.append(dbh_inches(attrs.get("DBH")))

        n = len(tree_id)
        return pa.table({
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["USLAN"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array(planted, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        })

    return transform


def iter_chunks():
    yield from iter_features(
        LAYER,
        out_fields=OUT_FIELDS,
        return_geometry=True,
        out_sr=4326,
        order_by="OBJECTID",
    )


if __name__ == "__main__":
    repeated = repeated_ids()
    table = stream_to_table(
        iter_chunks(), make_transform(repeated), keep=keep, label="Lansing OpenData"
    )
    sites = ", ".join(f"{n:,} {s}" for s, n in COUNTS.not_a_tree.most_common())
    statuses = ", ".join(f"{n:,} {s}" for s, n in COUNTS.status.most_common())
    print(
        f"Lansing ingest: dropped {sum(COUNTS.not_a_tree.values()):,} sites with no "
        f"standing tree ({sites}); {sum(COUNTS.status.values()):,} trees the GIS "
        f"audit could not place ({statuses}); {COUNTS.no_id} with no CityworksID, "
        f"{COUNTS.no_geometry} with no geometry.  {COUNTS.repeated_id_rows} rows on "
        f"{len(repeated)} repeated CityworksIDs keyed with their OBJECTID; nulled "
        f"{COUNTS.placeholder_dbh:,} placeholder DBHs (0, 150)",
        file=sys.stderr,
    )
    unparsed = sum(COUNTS.unparsed.values())
    if unparsed:
        print(
            f"Lansing ingest: {unparsed} species values unreadable: "
            f"{COUNTS.unparsed.most_common(10)}",
            file=sys.stderr,
        )
    if COUNTS.named and unparsed / COUNTS.named > MAX_UNPARSED_SHARE:
        raise RuntimeError(
            f"Lansing ingest: {unparsed} of {COUNTS.named} species values "
            "unreadable; the GENUS/SPECIES domains have changed"
        )
    table = validate_coordinates(table, city="Lansing", city_code="USLAN")
    table = enforce_tree_schema(table, city="Lansing", data_source="LANSING_OPENDATA")
    emit(table)

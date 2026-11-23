#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Baton Rouge, Louisiana's tree inventory, from the City-Parish's ArcGIS Online.

LICENCE: no licence stated; displayed with attribution only, not licensed for
reuse.  The item's `licenseInfo`, `accessInformation` and description are
empty, and the layer is not on the City-Parish's open-data sites (EBRGIS Open
Data, data.brla.gov), whose licences therefore do not cover it.

Source: "Tree Inventory Map1" (ArcGIS item 51b996ab35bc44bfb8d9e8d032b458c7,
owner `EBRGIS`, the East Baton Rouge City-Parish GIS portal; "Hosted feature
layer displaying the location of trees and associated attributes in EBRP"):
the service's only layer, `TreeInventoryCombined` (`FeatureServer/7`), 71,374
rows on 2026-11-22.  It is the City-Parish's Urban Forestry inventory merged
with the Baton Rouge Green programme plantings (`AssocProg`: Green Living
Roadways, City Citrus, Memorial Trees) in August 2023, and spans the parish
south of Baker (lat 30.24-30.56).  Its rows were last edited on 2023-09-14
(`editingInfo.dataLastEditDate`): it is the current official inventory, but
not one edited daily.  Geometry is stored in Web Mercator and read in WGS84
(`outSR=4326`); the `Latitude`/`Longitude` attributes disagree with it on
~550 rows and are not read.

**The id is the GlobalID** (`batonrouge-{guid}`), non-null and unique on every
row.  `TreeID` copies `OBJECTID_1` (69,299..140,779, the merge's renumbering)
on all but a handful of rows, and `Legacy_TreeId` repeats.

**Which rows are trees.**  `Status` (a coded domain: Alive, Dead, Downed,
Removed, Critical Root/Trunk Failure) is the current status: `Removed` and
`Downed` are dropped, as is a row carrying a `RemovalDate`.  `OldStatus` is
the pre-merge status, which the merge did not carry into `Status` (every
such row reads `Alive`): its `Stump`, `Removed`, `Debris`, `Sign` and
`Proposed site - *` rows (planting sites, no DBH, mostly no species) are
dropped too.  `Dead` stays (a dead tree is a tree).  An unseen status of
either kind refuses the build.

**Species is `LatinName`**, a coded domain whose codes are the names
themselves.  The crape myrtle is split into size classes (`Lagerstroemia
indica (MEDIUM)`, `(SMALL)`) which are stripped.  i-Tree's growth-form
placeholders (`Broadleaf Deciduous Small Other`, `Palm Evergreen Medium
Other`, ...) publish as the leaf-habit sentinels and `Palm`.  The Savannah and
Foster hollies, written `Ilex opaca x attenuata`, are `Ilex x attenuata`.  A
quoted cultivar is read into its own column (so `Citrus x meyeri 'Improved
Meyer's'` keeps its apostrophe).  Synonyms (`Sapium sebiferum`, `Carya
illinoensis`) fold in the shared schema.  A value that is not a Latin name
counts as unreadable; above 1% the build is refused.  `CommonName` is not
mapped.

**DBH is `DBH`, inches** (median 8, 99th percentile 45, a 0-480 range domain);
`0` is unmeasured and nulled.  `AlternateDBH` holds a handful of values
(24, 36, 60) that are not diameters and is not read.  `Height` (feet, mostly
0) has no canonical column.

**Planting date** is `DatePlanted`: 1899-12-30 (Excel's day zero) is stamped
on 70,656 rows and is nulled; the rest run 2013-2023.
"""

import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_date, esri_point, iter_features
from shared.ingest import (
    PALM_SPECIES,
    UNKNOWN_CONIFER_SPECIES,
    UNKNOWN_DECIDUOUS_SPECIES,
    UNKNOWN_EVERGREEN_SPECIES,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

SOURCE_URL = (
    "https://services.arcgis.com/KYvXadMcgf0K1EzK/arcgis/rest/services/"
    "Tree_Inventory_Map1/FeatureServer/7"
)
LAYER = FeatureLayer(SOURCE_URL, timeout=180)
OUT_FIELDS = "OBJECTID_1,GlobalID,LatinName,Status,OldStatus,DBH,DatePlanted,RemovalDate"
ID_PREFIX = "batonrouge"

KEPT_STATUSES = frozenset({"alive", "dead", "critical root/trunk failure"})
DROPPED_STATUSES = frozenset({"removed", "downed"})
# `OldStatus`, the pre-merge status; see the module docstring.
KEPT_OLD_STATUSES = frozenset({"", "alive", "dead"})
DROPPED_OLD_STATUSES = frozenset({
    "stump",
    "removed",
    "debris",
    "sign",
    "proposed site - small",
    "proposed site - medium",
    "proposed site - large",
})

# Dates before this are the 1899-12-30 placeholder.
EARLIEST_PLANT_DATE = date(1900, 1, 1)

MAX_UNPARSED_SHARE = 0.01

# i-Tree growth-form placeholders: `{Broadleaf|Conifer|Palm} {Deciduous|
# Evergreen} {Small|Medium|Large} Other`.
FORM_PLACEHOLDER = re.compile(
    r"^(broadleaf|conifer|palm) (deciduous|evergreen) (small|medium|large) other$"
)
FORM_SENTINELS = {
    ("broadleaf", "deciduous"): UNKNOWN_DECIDUOUS_SPECIES,
    ("broadleaf", "evergreen"): UNKNOWN_EVERGREEN_SPECIES,
    ("conifer", "evergreen"): UNKNOWN_CONIFER_SPECIES,
    ("conifer", "deciduous"): UNKNOWN_CONIFER_SPECIES,
    ("palm", "evergreen"): PALM_SPECIES,
    ("palm", "deciduous"): PALM_SPECIES,
}
SIZE_CLASS = re.compile(r"\s*\((small|medium|large)\)\s*$", re.IGNORECASE)
AUTHOR = re.compile(r"\s+L\.$")
# Genus, optional hybrid mark, then lower-case words (epithet, rank, `spp.`).
LATIN = re.compile(r"^[A-Z][a-z]+( x)?( [a-z][a-z.-]*)*$")

# Whole taxa the general reading gets wrong (lower-cased key).
TAXON_FIXES = {
    # The Savannah and Foster hollies: Ilex x attenuata (I. opaca x cassine).
    "ilex opaca x attenuata": "Ilex x attenuata",
}


def clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).replace("�", " ").replace("\xa0", " ")
    text = text.replace("‘", "'").replace("’", "'")
    text = " ".join(text.split())
    return text or None


def parse_species(raw) -> tuple[str | None, str | None, bool]:
    """(taxon, cultivar, whether the value failed to parse).

    Examples:
        "Lagerstroemia indica (MEDIUM)"          -> ("Lagerstroemia indica", None)
        "Ilex opaca x attenuata 'Savannah'"      -> ("Ilex x attenuata", "Savannah")
        "Citrus x meyeri 'Improved Meyer's'"     -> ("Citrus x meyeri", "Improved Meyer's")
        "Broadleaf Deciduous Small Other"        -> ("Unknown deciduous", None)
        "Palm Evergreen Medium Other"            -> ("Palm", None)
        "Salix babylonica L."                    -> ("Salix babylonica", None)
        " "                                      -> (None, None)
    """
    text = clean(raw)
    if text is None:
        return None, None, False
    form = FORM_PLACEHOLDER.match(text.lower())
    if form:
        return FORM_SENTINELS[(form.group(1), form.group(2))], None, False
    head, quote, tail = text.partition("'")
    cultivar = None
    if quote:
        cultivar = tail[:-1] if tail.endswith("'") else tail
        cultivar = " ".join(cultivar.split()) or None
        if cultivar and cultivar == cultivar.lower():
            cultivar = cultivar[:1].upper() + cultivar[1:]
    taxon = AUTHOR.sub("", SIZE_CLASS.sub("", head)).strip()
    taxon = TAXON_FIXES.get(taxon.lower(), taxon)
    if not LATIN.match(taxon):
        return None, None, True
    return taxon, cultivar, False


class Counts:
    no_geometry = 0
    no_id = 0
    dropped: Counter = Counter()
    unparsed: Counter = Counter()
    named = 0
    placeholder_date = 0


COUNTS = Counts()


def keep(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    status = (clean(attrs.get("Status")) or "").lower()
    old = (clean(attrs.get("OldStatus")) or "").lower()
    if status not in KEPT_STATUSES and status not in DROPPED_STATUSES:
        raise RuntimeError(
            f"Baton Rouge ingest: unseen Status {attrs.get('Status')!r}; "
            "decide whether it is a standing tree"
        )
    if old not in KEPT_OLD_STATUSES and old not in DROPPED_OLD_STATUSES:
        raise RuntimeError(
            f"Baton Rouge ingest: unseen OldStatus {attrs.get('OldStatus')!r}; "
            "decide whether it is a standing tree"
        )
    if status in DROPPED_STATUSES:
        COUNTS.dropped[f"Status {status}"] += 1
        return False
    if old in DROPPED_OLD_STATUSES:
        COUNTS.dropped[f"OldStatus {old}"] += 1
        return False
    if attrs.get("RemovalDate") is not None:
        COUNTS.dropped["RemovalDate set"] += 1
        return False
    if not str(attrs.get("GlobalID") or "").strip("{} "):
        COUNTS.no_id += 1
        return False
    lat, lon = esri_point(feature.get("geometry"))
    if lat is None or lon is None:
        COUNTS.no_geometry += 1
        return False
    return True


def plant_date(raw) -> date | None:
    value = esri_ms_to_date(raw)
    if value is not None and value < EARLIEST_PLANT_DATE:
        COUNTS.placeholder_date += 1
        return None
    return value


def iter_chunks():
    yield from iter_features(
        LAYER,
        out_fields=OUT_FIELDS,
        return_geometry=True,
        out_sr=4326,
        order_by="OBJECTID_1",
    )


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
        raw = attrs.get("LatinName")
        name, cv, failed = parse_species(raw)
        if clean(raw):
            COUNTS.named += 1
        if failed:
            COUNTS.unparsed[clean(raw)] += 1
        guid = str(attrs["GlobalID"]).strip().strip("{}").lower()
        tree_id.append(f"{ID_PREFIX}-{guid}")
        species.append(name)
        cultivar.append(cv)
        planted.append(plant_date(attrs.get("DatePlanted")))
        latitude.append(lat)
        longitude.append(lon)
        diameter = attrs.get("DBH")
        dbh.append(float(diameter) if diameter is not None and diameter > 0 else None)

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["USBTR"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "cultivar": pa.array(cultivar, type=pa.string()),
        "plant_date": pa.array(planted, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


if __name__ == "__main__":
    table = stream_to_table(iter_chunks(), transform, keep=keep, label="Baton Rouge OpenData")
    dropped = ", ".join(f"{n:,} {why}" for why, n in COUNTS.dropped.most_common())
    print(
        f"Baton Rouge ingest: dropped {sum(COUNTS.dropped.values()):,} rows that are "
        f"not standing trees ({dropped}), {COUNTS.no_id} with no GlobalID, "
        f"{COUNTS.no_geometry} with no geometry; nulled {COUNTS.placeholder_date:,} "
        "placeholder (1899-12-30) planting dates",
        file=sys.stderr,
    )
    unparsed = sum(COUNTS.unparsed.values())
    if unparsed:
        print(
            f"Baton Rouge ingest: {unparsed} species values unreadable: "
            f"{COUNTS.unparsed.most_common(10)}",
            file=sys.stderr,
        )
    if COUNTS.named and unparsed / COUNTS.named > MAX_UNPARSED_SHARE:
        raise RuntimeError(
            f"Baton Rouge ingest: {unparsed} of {COUNTS.named} species values "
            "unreadable; the LatinName field's format has changed"
        )
    table = validate_coordinates(table, city="Baton Rouge", city_code="USBTR")
    table = enforce_tree_schema(
        table, city="Baton Rouge", data_source="BATON_ROUGE_OPENDATA"
    )
    emit(table)

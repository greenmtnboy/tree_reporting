#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy", "tzdata"]
# ///
"""Christchurch, NZ's council tree inventory.

Source: "Tree (OpenData)" (ArcGIS item 0c557c04453e435ba1e50389db0ede90,
owner `CCCSpatial`), `OpenData/Tree/FeatureServer/0` on Christchurch City
Council's own ArcGIS Server, gis.ccc.govt.nz; listed on the council's open
data hub.  The item's licence: "CCC - Open Data - Use of this dataset is
permitted in accordance with the Creative Commons Attribution 4.0
International (CC BY 4.0) License"; credit "Christchurch City Council".
186,372 rows on 2026-11-16, stored in NZTM (EPSG:2193) and read reprojected
to WGS84 (`outSR=4326`).

**Which rows are trees.**  `ServiceStatus` (no coded domain; read by value):
Current 129,631, Removed 49,049, Defects Liability 4,954, Stump 2,683,
Special tree 55.  Removed trees and stumps stay in the layer and are dropped
here.  `Defects Liability` is a newly planted tree still inside its planting
contractor's defects-liability period -- a tree in the ground -- and is kept,
as is `Special tree`.  134,640 rows are kept.  `Ownership` (CCC 168,606,
Private 14,020, NZTA 2,750) is not a filter: the private rows are trees the
council records on private land (District Plan and subdivision protected
trees), still real trees at a place.

**Id is `TreeID`, the layer's OID** (`chc-{TreeID}`).  The layer has no
GlobalID, and its asset-system reference `SAPInternalReference` is both null
(11,711 rows; 5,647 kept ones) and repeated (over 2,000 values on two rows,
among kept rows too); `ContractorExternalReference` is mostly null and also
repeats.  `TreeID` is the OID of the council's internal editing layer itself,
not a reload counter: the `Photo` column's attachment links point at
`InternalData/Tree/FeatureServer/0/{TreeID}/attachments/...` with the same
number, and TreeID runs 1-188,352 over 186,372 rows (retired trees are kept
as `Removed`, not deleted and renumbered).

The service serves some features twice: on 2026-11-16, 2,092 kept TreeIDs
came back as two rows each (`returnIdsOnly` lists the id twice, and
`returnCountOnly` counts both -- the 186,372 includes them), the two copies
identical in every field and in geometry.  The second copy is dropped with a
count.  A TreeID whose copies differ is a clash rather than a repeat and
every copy of it is dropped; more than `MAX_CONFLICTING` of those means the
id has stopped being the feature id, and the ingest refuses.

**Species is `Species`**, written `Genus [x] epithet ['Cultivar'] (Common
name)`; the trailing parenthesised common name (one, `Pinus radiata (Radiata
Pine (w))`, nests a second) is cut at the first ` (` and never mapped.
`Genus`, `BotanicName`, `Cultivar`, `Variety`, `SubSpecies` and `IsHybrid`
are the same name in parts, and agree with it.  The portal's own `Cultivar`
column is the cultivar (so `Populus sp. 'Crow's Nest'` keeps its apostrophe);
the shared schema rules do the rest (`var.`/`subsp.` truncation, `sp.` to the
genus, synonyms).  `NAME_FIXES` are the names those rules cannot read:

* `Prunus x sp. 'Yedoensis'` (3,367 trees, the commonest cherry) writes the
  hybrid's epithet as a cultivar; it is Prunus x yedoensis, which the
  same layer also spells out (`Prunus x yedoensis 'Chinese Brocade'`).
  `Hoheria x sp. 'Angustifolia'` likewise is Hoheria angustifolia.
* `Populus x hybrid`, `Magnolia x hybrid`, `Nothofagus x species`,
  `Ligustrum x species`: an unnamed hybrid, published as the genus.
* `Sophora x tetraptera` (2,406), `Coprosma x areolata`, `Corokia x
  cotoneaster`, `Acer x platanoides`, ...: the layer's `IsHybrid` flag puts a
  hybrid sign in front of species that are not hybrids (WCVP accepts the
  plain binomial and has no nothospecies of that name).  The sign is dropped.

**DBH is metres** (`DiameterAtBreastHeight`; median 0.20, 95th percentile
1.0, the giant sequoias 2.5-2.85), but several hundred rows were typed in
centimetres: from 6 up the values are 10, 15, 20, 25, 30 ... on juvenile and
semi-mature trees 3-12 m tall.  A value of 6 or more is read as centimetres;
one from 3 up to 6 is ambiguous (a 3 cm sapling or a 3 m veteran, both
present) and is nulled; one below 3 is metres.  Zero is no measurement.

**`CrownSpread` is the crown width in metres** (median 3.4, 95th percentile
15); zero (4,729 rows) is no measurement and is nulled.  `Height` has no
column in the published schema and is not read.

**`PlantedDate` is midnight New Zealand time** stored as UTC (2005-02-22 is
`2005-02-21T11:00Z`), so the date is read in Pacific/Auckland: the UTC date
would put every planting a day early.  1900-01-01 (9,238 kept rows, by far
the commonest value) is the inventory's stamp for an unknown date and is
nulled.  The many 1 June / 1 July dates are year-precision records (NZ's
planting season and financial year), kept as written; the schema nulls dates
before 1500 or in the future.
"""

import math
import sys
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.compute as pc

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_datetime, esri_point, iter_features
from shared.ingest import (
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://gis.ccc.govt.nz/server/rest/services/OpenData/Tree/FeatureServer/0",
    timeout=180,
)
FIELDS = (
    "TreeID,ServiceStatus,Species,Cultivar,PlantedDate,"
    "DiameterAtBreastHeight,CrownSpread"
)

# `ServiceStatus` values that are a tree standing at its point.  Anything else
# (Removed, Stump) is dropped, and an unknown value raises: the layer has
# grown a status this ingest has not classified.
STANDING = frozenset({"Current", "Defects Liability", "Special tree"})
GONE = frozenset({"Removed", "Stump"})

# TreeIDs served twice with different contents above which the id is no
# longer trusted as the grain (see the module docstring).
MAX_CONFLICTING = 100

LOCAL_TZ = ZoneInfo("Pacific/Auckland")

# The inventory's stamp for "planting date unknown".
PLACEHOLDER_PLANT_DATE = date(1900, 1, 1)

# DBH, metres; see the module docstring for the centimetre rows.
DBH_CM_FROM = 6.0
DBH_AMBIGUOUS_FROM = 3.0
CM_PER_INCH = 2.54

# Raw scientific names (the common name cut off) that the shared rules cannot
# read, mapped to (species, cultivar).  The cultivar replaces the portal's.
NAME_FIXES: dict[str, tuple[str, str | None]] = {
    "Prunus x sp. 'Yedoensis'": ("Prunus x yedoensis", None),
    "Hoheria x sp. 'Angustifolia'": ("Hoheria angustifolia", None),
    "Populus x hybrid": ("Populus", None),
    "Magnolia x hybrid": ("Magnolia", None),
    "Nothofagus x species": ("Nothofagus", None),
    "Ligustrum x species 'Festeri'": ("Ligustrum", "Festeri"),
}

# The portal's cultivar spellings that are typos of the registered name
# (`Quercus robur 'Fastigata'`, 1,578 trees, beside 1,500 spelled right).
CULTIVAR_FIXES = {"Fastigata": "Fastigiata"}

# `Genus x epithet` where WCVP has the binomial only without a hybrid sign
# (checked against the published WCVP names, 2026-11-16): the layer's
# IsHybrid flag wrote a hybrid sign that is not there.  Pseudopanax
# crassifolium has no record either way (WCVP: crassifolius); the sign goes
# and the epithet is left to the shared misspelling map.
FALSE_HYBRIDS = frozenset({
    ("Sophora", "tetraptera"),
    ("Coprosma", "areolata"),
    ("Corokia", "cotoneaster"),
    ("Acer", "platanoides"),
    ("Juniperus", "chinensis"),
    ("Tilia", "platyphyllos"),
    ("Camellia", "japonica"),
    ("Ilex", "aquifolium"),
    ("Magnolia", "grandiflora"),
    ("Magnolia", "obovata"),
    ("Lomatia", "ferruginea"),
    ("Banksia", "serrata"),
    ("Eriobotrya", "deflexa"),
    ("Pseudopanax", "crassifolium"),
    ("Lophomyrtus", "obcordata"),
    ("Lophomyrtus", "bullata"),
    ("Euonymus", "lucidus"),
    ("Feijoa", "sellowiana"),
})


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def scientific_name(raw: str | None) -> str | None:
    """`Species` without its trailing `(Common name)`, quotes tidied."""
    text = clean(raw)
    if text is None:
        return None
    text = text.split(" (", 1)[0].strip()
    # `Acer palmatum 'Seiryu''`: a doubled closing quote.
    return text.replace("''", "'") or None


def drop_false_hybrid(name: str) -> str:
    parts = name.split(" ")
    if len(parts) >= 3 and parts[1] in ("x", "X", "×") and (parts[0], parts[2]) in FALSE_HYBRIDS:
        return " ".join([parts[0], *parts[2:]])
    return name


def species_and_cultivar(raw_species, raw_cultivar) -> tuple[str | None, str | None]:
    name = scientific_name(raw_species)
    cultivar = clean(raw_cultivar)
    cultivar = CULTIVAR_FIXES.get(cultivar, cultivar)
    if name is None:
        return None, None
    if name in NAME_FIXES:
        return NAME_FIXES[name]
    return drop_false_hybrid(name), cultivar


def local_date(value) -> date | None:
    stamp = esri_ms_to_datetime(value)
    if stamp is None:
        return None
    day = stamp.astimezone(LOCAL_TZ).date()
    return None if day == PLACEHOLDER_PLANT_DATE else day


def dbh_inches(value) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    if v >= DBH_CM_FROM:
        return v / CM_PER_INCH
    if v >= DBH_AMBIGUOUS_FROM:
        return None
    return v * 100.0 / CM_PER_INCH


def crown_metres(value) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


class Counts:
    gone = 0
    no_id = 0
    duplicate = 0


COUNTS = Counts()

# TreeID -> the row as first read, to tell a served-twice copy from a clash.
SEEN: dict[int, tuple] = {}
# TreeIDs served twice with different contents: neither copy is published.
CONFLICTING: set[int] = set()


def signature(feature: dict) -> tuple:
    attrs = feature.get("attributes") or {}
    geom = feature.get("geometry") or {}
    return (tuple(sorted(attrs.items())), geom.get("x"), geom.get("y"))


def keep(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    status = clean(attrs.get("ServiceStatus"))
    if status in STANDING:
        oid = attrs.get("TreeID")
        if oid is None:
            COUNTS.no_id += 1
            return False
        oid = int(oid)
        sig = signature(feature)
        first = SEEN.get(oid)
        if first is None:
            SEEN[oid] = sig
            return True
        COUNTS.duplicate += 1
        if first != sig:
            CONFLICTING.add(oid)
        return False
    if status in GONE:
        COUNTS.gone += 1
        return False
    raise RuntimeError(
        f"Christchurch ingest: unknown ServiceStatus {status!r} on TreeID "
        f"{attrs.get('TreeID')}; classify it in STANDING or GONE"
    )


def iter_chunks():
    yield from iter_features(
        LAYER,
        out_fields=FIELDS,
        return_geometry=True,
        out_sr=4326,
        order_by="TreeID",
    )


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        name, cv = species_and_cultivar(attrs.get("Species"), attrs.get("Cultivar"))
        tree_id.append(f"chc-{int(attrs['TreeID'])}")
        species.append(name)
        cultivar.append(cv)
        plant_date.append(local_date(attrs.get("PlantedDate")))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(dbh_inches(attrs.get("DiameterAtBreastHeight")))
        crown.append(crown_metres(attrs.get("CrownSpread")))

    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["NZCHC"] * len(tree_id), type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "cultivar": pa.array(cultivar, type=pa.string()),
        "plant_date": pa.array(plant_date, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        "crown_width_m": pa.array(crown, type=pa.float64()),
    })


if __name__ == "__main__":
    table = stream_to_table(iter_chunks(), transform, keep=keep, label="Christchurch OpenData")
    if CONFLICTING:
        ids = pa.array([f"chc-{oid}" for oid in sorted(CONFLICTING)], type=pa.string())
        table = table.filter(pc.invert(pc.is_in(table.column("tree_id"), value_set=ids)))
    if len(CONFLICTING) > MAX_CONFLICTING:
        raise RuntimeError(
            f"Christchurch ingest: {len(CONFLICTING)} TreeIDs are served twice with "
            "different contents; TreeID no longer looks like the feature id"
        )
    print(
        f"Christchurch ingest: dropped {COUNTS.gone} removed trees and stumps, "
        f"{COUNTS.no_id} rows with no TreeID, {COUNTS.duplicate} second copies of "
        f"a TreeID ({len(CONFLICTING)} TreeIDs whose copies differ, all copies dropped)",
        file=sys.stderr,
    )
    table = validate_coordinates(table, city="Christchurch", city_code="NZCHC")
    table = enforce_tree_schema(
        table, city="Christchurch", data_source="CHRISTCHURCH_OPENDATA"
    )
    emit(table)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Boise, Idaho's park and street trees, from Boise Parks and Recreation.

Source: "Parks and Recreation Managed Park and Street Trees" (ArcGIS item
61e4dc96bc0745ecb42c3f3892728bd6, owner `Boise_GIS`, "This dataset was
created and is maintained by Parks and Recreation staff"), the hosted service
`BPR_Park_And_Street_Trees/FeatureServer` with two point layers: `/0` Park
Trees (14,974 rows on 2026-11-22) and `/1` Street Trees (19,711).  Both are
read.  It is listed on the City's open-data site, opendata.cityofboise.org.

LICENCE: Creative Commons Attribution 4.0 International.  The item's own
`licenseInfo` is only the City's no-warranty disclaimer, but the open-data
site (Hub site item ee109a20969d4317918cfead374dbb2f,
https://opendata.cityofboise.org/) states: "Open means open! This site and
the data it presents are open, public, and free to use. Except where
otherwise noted, content on this site is licensed under a Creative Commons
Attribution 4.0 International license."  Attribution: City of Boise.

**The id is `GlobalID`** (`boise-{guid}`), the layers' system GlobalID,
non-null and unique over both layers together (34,685 rows, checked
2026-11-22).  `OBJECTID` is sparse (47,915..215,993 on the park layer,
88,337..378,662 on the street layer): layers edited in place, not reloaded.

**Species: the street layer splits it into `Genus` and `Species`**, both
coded-value domains holding the Latin name in capitals (`QUERCUS` / `RUBRA`,
`ACER` / `PLATANOIDES 'CRIMSON KING'`, `SPECIES` for a tree identified only to
genus).  **The park layer has no `Genus` column** -- only the epithet and the
`Common_Name` domain the two layers share ("Oak, Northern Red").  A park
row's genus is therefore read, in order, from:

  1. the street layer's own pairing of that `Common_Name` with a `Genus`
     (the dominant genus, at least 80% of the street rows carrying the name),
     then the same for the name without its cultivar suffix
     ("Ash, Green-Marshalls Seedless" -> "Ash, Green") -- the publisher's
     answer, read live;
  2. `PARK_GENUS` below, for the ~600 park rows whose name the street layer
     never uses: a genus per head word of the publisher's own common-name
     domain ("Willow, ..." -> Salix), with the few heads that name several
     genera ("Cedar", "Tamarack") keyed by the full name.  Only the genus
     comes from it; the epithet is always the publisher's.
A park row with neither is published `Unknown` and counted; the ingest
refuses to publish if that passes 2% of park rows (a new head word in the
domain).  `Common_Name` itself is never mapped as a species.

The street layer's `Genus` is occasionally the wrong pick from its domain
(`GYMNOCLADUS` / `X ACERIFOLIA` on 13 "Planetree, London" rows, `MALUS` or
`PRUNUS` / `CALLERYANA` on 27 Callery pears).  Where a row's genus disagrees
with the dominant genus of its `Common_Name` *and* that dominant genus is
published with the same epithet elsewhere in the layer, the dominant genus is
used (44 rows on 2026-11-22); both the common name and the epithet then agree
against the one field.  Otherwise the row's own genus stands.

The cultivar is the quoted part of the epithet where there is one
(`PLATANOIDES 'CRIMSON KING'`), else the suffix of `Common_Name` after its
first hyphen ("Maple, Norway-Crimson King", "Crabapple-Radiant"): the domain
writes every selection that way, and the cultivar is a fact about the tree,
not a common name.

**DBH is inches** (`Tree_Diameter`, a small integer: street median 12,
park median 5, 99th percentile 48).  Zero is nulled by the schema.

**`Installation_Date` is published only where the tree could have been
planted then.**  It is a date stored at UTC midnight (a few rows carry an
edit time), so its UTC date is read.  It is the asset system's install date,
and for trees that were already standing when they were inventoried it is
the inventory date: the street rows dated January-February 2008 (3,800 of
them) have a median diameter of 17 in, the 1981 rows 22 in, while rows dated
2018 onwards have a median of 2 in.  So a date is kept only when the tree's
diameter is at most `STOCK_DBH_IN` (planting stock) plus `MAX_GROWTH_IN_PER_YEAR`
per year since -- a generous bound no planted street tree outgrows -- and
nulled otherwise (4,093 rows on 2026-11-22).  A row with no diameter keeps
its date.

**Removed trees are dropped**: every row is `Status = Active` today, but 7
carry a `Removal_Date`; a `Retired` row (the domain has the value) would be
dropped too.  Neither layer flags vacant sites or stumps.
"""

import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_date, esri_point, iter_features
from shared.ingest import emit, enforce_tree_schema, validate_coordinates

SERVICE = (
    "https://services1.arcgis.com/WHM6qC35aMtyAAlN/arcgis/rest/services/"
    "BPR_Park_And_Street_Trees/FeatureServer"
)
PARK = FeatureLayer(f"{SERVICE}/0", timeout=300)
STREET = FeatureLayer(f"{SERVICE}/1", timeout=300)
PARK_FIELDS = "OBJECTID,GlobalID,Status,Common_Name,Species,Installation_Date,Tree_Diameter,Removal_Date"
STREET_FIELDS = PARK_FIELDS + ",Genus"

# An install date is kept only if the recorded diameter could have grown from
# planting stock since then (see the module docstring).
STOCK_DBH_IN = 3.0
MAX_GROWTH_IN_PER_YEAR = 1.0
TODAY = date.today()

# A genus must hold at least this share of the street rows naming a
# Common_Name before a park row with that name inherits it.
DOMINANT_SHARE = 0.8

# Park rows that resolve to no genus at all, above which the ingest refuses.
MAX_UNRESOLVED_PARK_SHARE = 0.02

# Genus per head word of Boise's Common_Name domain (the text before the
# comma, after the cultivar suffix is cut), for park rows whose name the
# street layer never carries.  Full names come first where a head word names
# several genera.
PARK_GENUS_BY_NAME = {
    "cedar, northern white": "Thuja",
    "cedar, lebanon": "Cedrus",
    "redcedar, western": "Thuja",
    "tamarack": "Larix",
    "amur corktree": "Phellodendron",
    "osage orange": "Maclura",
    "cucumber tree": "Magnolia",
    "fragrant epaulette": "Pterostyrax",
    "bald cypress": "Taxodium",
    "redwood, coast": "Sequoia",
}
PARK_GENUS = {
    "alder": "Alnus",
    "ash": "Fraxinus",
    "birch": "Betula",
    "birdcherry": "Prunus",
    "buckeye": "Aesculus",
    "cherry": "Prunus",
    "corkwood": "Leitneria",
    "cottonwood": "Populus",
    "crabapple": "Malus",
    "elm": "Ulmus",
    "falsecypress": "Chamaecyparis",
    "fir": "Abies",
    "hawthorn": "Crataegus",
    "hazel": "Corylus",
    "hickory": "Carya",
    "honeylocust": "Gleditsia",
    "juniper": "Juniperus",
    "larch": "Larix",
    "linden": "Tilia",
    "magnolia": "Magnolia",
    "maple": "Acer",
    "nectarine": "Prunus",
    "oak": "Quercus",
    "pear": "Pyrus",
    "pine": "Pinus",
    "poplar": "Populus",
    "sassafras": "Sassafras",
    "serviceberry": "Amelanchier",
    "spruce": "Picea",
    "sweetgum": "Liquidambar",
    "tuliptree": "Liriodendron",
    "willow": "Salix",
    "zelkova": "Zelkova",
}

# Epithets the domain spells so that the schema would cut them short.
EPITHET_FIXES = {
    "BORISII REGIS": "BORISII-REGIS",  # Abies borisii-regis
}

DROPPED: Counter = Counter()
CLEANED: Counter = Counter()


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("�", "'").split())
    return text or None


def base_name(common: str | None) -> str | None:
    """The common name without its cultivar suffix: "Maple, Norway-Crimson King" -> "Maple, Norway"."""
    if not common:
        return None
    return common.split("-", 1)[0].strip() or None


def name_cultivar(common: str | None) -> str | None:
    """The selection a Common_Name carries after its first hyphen, if any."""
    if not common or "-" not in common:
        return None
    return common.split("-", 1)[1].strip() or None


def dominant(counter: Counter | None) -> str | None:
    if not counter:
        return None
    genus, n = counter.most_common(1)[0]
    return genus if n / sum(counter.values()) >= DOMINANT_SHARE else None


def keep(rec: dict) -> bool:
    if not clean(rec.get("GlobalID")):
        DROPPED["no GlobalID"] += 1
        return False
    if clean(rec.get("Status")) == "Retired":
        DROPPED["retired"] += 1
        return False
    if rec.get("Removal_Date") is not None:
        DROPPED["removal date"] += 1
        return False
    return True


def species_name(genus: str | None, epithet: str | None) -> str | None:
    """Genus + epithet as published; `SPECIES` means identified to genus only."""
    if not genus:
        return None
    if epithet:
        epithet = EPITHET_FIXES.get(epithet.upper(), epithet)
    if not epithet or epithet.upper() == "SPECIES":
        return genus.capitalize()
    return f"{genus.capitalize()} {epithet.lower()}"


def plant_date(stamp, diameter) -> date | None:
    """The install date, unless the diameter says the tree was already standing."""
    planted = esri_ms_to_date(stamp)
    if planted is None or diameter is None:
        return planted
    years = max((TODAY - planted).days / 365.25, 0.0)
    if float(diameter) > STOCK_DBH_IN + MAX_GROWTH_IN_PER_YEAR * years:
        CLEANED["install date older than the tree's size allows"] += 1
        return None
    return planted


def fetch(layer: FeatureLayer, fields: str) -> list[dict]:
    return [
        f
        for page in iter_features(layer, out_fields=fields, return_geometry=True, out_sr=4326)
        for f in page
    ]


class GenusLookup:
    """The genus each Common_Name carries on the street layer."""

    def __init__(self, street: list[dict]) -> None:
        self.by_name: dict[str, Counter] = defaultdict(Counter)
        self.by_base: dict[str, Counter] = defaultdict(Counter)
        self.pairs: Counter = Counter()
        for feature in street:
            rec = feature.get("attributes") or {}
            genus = clean(rec.get("Genus"))
            common = clean(rec.get("Common_Name"))
            if genus and common:
                self.by_name[common][genus] += 1
                self.by_base[base_name(common)][genus] += 1
            if genus:
                self.pairs[(genus, clean(rec.get("Species")))] += 1

    def street_genus(self, rec: dict) -> str | None:
        """A street row's genus, corrected where its name and epithet both disagree."""
        genus = clean(rec.get("Genus"))
        common = clean(rec.get("Common_Name"))
        epithet = clean(rec.get("Species"))
        usual = dominant(self.by_name.get(common)) if common else None
        if genus and usual and genus != usual and self.pairs[(usual, epithet)] > 0:
            CLEANED["street genus corrected"] += 1
            return usual
        return genus

    def park_genus(self, common: str | None) -> str | None:
        if not common:
            return None
        found = dominant(self.by_name.get(common)) or dominant(self.by_base.get(base_name(common)))
        if found:
            CLEANED["park genus from street layer"] += 1
            return found
        base = base_name(common).lower()
        head = base.split(",", 1)[0].strip()
        found = PARK_GENUS_BY_NAME.get(base) or PARK_GENUS_BY_NAME.get(head) or PARK_GENUS.get(head)
        if found:
            CLEANED["park genus from head word"] += 1
        return found


def transform(features: list[dict], lookup: GenusLookup, *, park: bool) -> pa.Table:
    cols: dict[str, list] = {
        k: []
        for k in ("tree_id", "species", "cultivar", "plant_date", "latitude", "longitude", "dbh")
    }
    for feature in features:
        rec = feature.get("attributes") or {}
        if not keep(rec):
            continue
        common = clean(rec.get("Common_Name"))
        epithet = clean(rec.get("Species"))
        if park:
            genus = lookup.park_genus(common)
            if genus is None:
                CLEANED["park genus unresolved"] += 1
        else:
            genus = lookup.street_genus(rec)
        name = species_name(genus, epithet)
        cultivar = None
        if name and "'" not in name:
            cultivar = name_cultivar(common)
        diameter = rec.get("Tree_Diameter")
        planted = plant_date(rec.get("Installation_Date"), diameter)
        lat, lon = esri_point(feature.get("geometry"))
        guid = clean(rec["GlobalID"]).strip("{}").lower()
        cols["tree_id"].append(f"boise-{guid}")
        cols["species"].append(name)
        cols["cultivar"].append(cultivar)
        cols["plant_date"].append(planted)
        cols["latitude"].append(lat)
        cols["longitude"].append(lon)
        cols["dbh"].append(float(diameter) if diameter is not None else None)

    n = len(cols["tree_id"])
    return pa.table(
        {
            "tree_id": pa.array(cols["tree_id"], type=pa.string()),
            "city": pa.array(["USBOI"] * n, type=pa.string()),
            "species": pa.array(cols["species"], type=pa.string()),
            "cultivar": pa.array(cols["cultivar"], type=pa.string()),
            "plant_date": pa.array(cols["plant_date"], type=pa.date32()),
            "latitude": pa.array(cols["latitude"], type=pa.float64()),
            "longitude": pa.array(cols["longitude"], type=pa.float64()),
            "diameter_at_breast_height": pa.array(cols["dbh"], type=pa.float64()),
        }
    )


def build() -> pa.Table:
    street = fetch(STREET, STREET_FIELDS)
    park = fetch(PARK, PARK_FIELDS)
    if not street or not park:
        raise RuntimeError(f"Boise: empty layer (street {len(street)}, park {len(park)})")
    lookup = GenusLookup(street)
    table = pa.concat_tables(
        [transform(street, lookup, park=False), transform(park, lookup, park=True)]
    )
    unresolved = CLEANED["park genus unresolved"]
    if unresolved > MAX_UNRESOLVED_PARK_SHARE * len(park):
        raise RuntimeError(
            f"Boise: {unresolved} of {len(park)} park rows resolve to no genus; "
            f"a new Common_Name head word needs a PARK_GENUS entry"
        )
    print(
        f"Boise: read {len(street)} street and {len(park)} park rows; "
        f"dropped {dict(DROPPED)}; cleaned {dict(CLEANED)}",
        file=sys.stderr,
    )
    missing = table["latitude"].null_count
    if missing:
        import pyarrow.compute as pc

        table = table.filter(pc.is_valid(table["latitude"]))
        print(f"Boise: dropped {missing} row(s) without a geometry", file=sys.stderr)
    return table


if __name__ == "__main__":
    table = build()
    table = validate_coordinates(table, city="Boise", city_code="USBOI")
    table = enforce_tree_schema(table, city="Boise", data_source="BOISE_OPENDATA")
    emit(table)

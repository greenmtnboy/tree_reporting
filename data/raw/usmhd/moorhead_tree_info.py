#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Moorhead, Minnesota's City tree inventory.

No licence stated; displayed with attribution only, not licensed for reuse.
The item's licence field is a disclaimer ("made available as a public
service, and is to be used for reference purposes only ... makes no
representation or warranties ... with respect to the use or reuse of the
data"), https://www.arcgis.com/home/item.html?id=efd9f39819d54020b610ef0f5f0c0c04;
the City's GIS page (moorheadmn.gov, "Geographic Information Systems")
repeats it and states no other terms.

Source: "Treeplotter_Inventory" (ArcGIS item efd9f39819d54020b610ef0f5f0c0c04,
owner `CityOfMoorheadMN`, credited to "City of Moorhead GIS"), layer 2
"Trees" of the hosted FeatureServer: 35,874 rows on 2026-11-23, an export of
the City's TreePlotter (PlanIT Geo) inventory.  Layers 0 and 1 are work
orders and service requests, not trees.  The item says it is "updated
weekly", but every row carries the same `created_date` (2024-08-02) and the
layer's `editingInfo` has not moved since: one bulk load, not refreshed
since.  It is still the City's own and the only official inventory (the
`ESRI_Community_Maps_MHD/TreeBush` layer is a basemap contribution with no
species).

**Which rows are trees.**  `status` is the TreePlotter site status:
`Alive` and `Dead` are trees (a dead tree is a tree standing there and
stays); `Removed`, `Stump` and the three `Proposed Site - *` planting sites
are not and are dropped, as is any row TreePlotter marks `archived = true`
(removed from the live inventory; 7 of them still say `Alive`).  The 18 rows
with an empty status are kept when they name a species.  394 `Alive` rows
are park features, not trees -- `species_common` `Football Field`, `Soccer
Field ...`, `Lacrosse Field`, `Trail` -- and are dropped, as are rows with
neither a name nor a diameter (an empty record).  Every drop is counted on
stderr.

**The id is the GlobalID** (`moorhead-{guid}`), non-null and unique over the
whole layer (checked 2026-11-23); a repeated GlobalID refuses the build.
TreePlotter's own `GmlID` ("Primary ID") repeats on 2,937 values, so it is
not an id; OBJECTIDs are a dense block from the bulk load.

**Species.**  The layer has no Latin name: `species_common` (an English
common name, often a cultivar trade name: `Emerald Lustre Norway Maple`) and
`species_genus` (a Latin genus, a few misspelt: `Quarcus`, `Aesculas`,
`Vibernum`).  `species_cultivar` is empty.  The name is read, in order,
through `CULTIVAR_NAMES` below (this inventory's own cultivar trade names ->
taxon and cultivar: a hybrid cultivar resolves to its genus, the shared
module's rule), then the shared English table
(`shared.species.english`), and when neither knows it, the row's recorded
genus (`GENUS_FIXES` corrects the misspellings; a family or a placeholder
publishes Unknown).  A genus is an answer.  `Cedar` resolves through the
shared table to *Thuja occidentalis* (northern white-cedar is native here and
*Cedrus* is not hardy in USDA zone 4), although TreePlotter stamped its
genus `Cedrus`.

**DBH is `dbh_exact`, whole inches** (1..48 with a tail to 120 on the
cottonwoods and silver maples; the `dbh` class column agrees).  A zero
(132 rows) is no measurement and publishes as null.  The layer's
`tree_height` (feet) has no column in the schema; it records no crown spread.

**No planting date.**  `date_added_calc` is when the record was created in
TreePlotter (2019 onwards), not when the tree was planted.
"""

import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.species.english import species_from_common_name
from shared.ingest import (
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services.arcgis.com/t2tplTEKxuL7eSvF/arcgis/rest/services/"
    "Treeplotter_Inventory/FeatureServer/2",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,GlobalID,archived,status,species_common,species_genus,dbh_exact"
ID_PREFIX = "moorhead"

TREE_STATUSES = frozenset({"alive", "dead", ""})
NOT_TREE_STATUSES = frozenset(
    {"removed", "stump", "proposed site - small", "proposed site - medium", "proposed site - large"}
)
# `species_common` values that are park features, not trees.
NOT_TREES = frozenset(
    {
        "football field",
        "flag football field",
        "soccer field small",
        "soccer field medium",
        "soccer field large",
        "lacrosse field",
        "trail",
    }
)
NO_NAME = frozenset({"", "n/a"})

# Refuse to publish when more than this share of named rows resolve to nothing.
MAX_UNRESOLVED_SHARE = 0.01

# `species_common` (lower-cased) -> (taxon, cultivar).  The inventory's cultivar
# trade names, which the shared English table does not read (it lifts only a
# quoted selection).  Hybrid cultivars with no binomial resolve to the genus.
CULTIVAR_NAMES: dict[str, tuple[str, str | None]] = {
    # Acer
    "emerald lustre norway maple": ("Acer platanoides", "Emerald Lustre"),
    "royal red maple": ("Acer platanoides", "Royal Red"),
    "crimson king maple": ("Acer platanoides", "Crimson King"),
    "deborah maple": ("Acer platanoides", "Deborah"),
    "sienna glen maple": ("Acer x freemanii", "Sienna Glen"),
    "autumn blaze maple": ("Acer x freemanii", "Autumn Blaze"),
    "matador maple": ("Acer x freemanii", "Matador"),
    "firefall maple": ("Acer x freemanii", "Firefall"),
    "state street maple": ("Acer miyabei", "State Street"),
    "green mountain maple": ("Acer saccharum", "Green Mountain"),
    "northwoods red maple": ("Acer rubrum", "Northwood"),
    "pattern perfect tatarian maple": ("Acer tataricum", "Pattern Perfect"),
    "hot wings tatarian maple": ("Acer tataricum", "Hot Wings"),
    "sensation maple": ("Acer negundo", "Sensation"),
    "korean maple": ("Acer pseudosieboldianum", None),
    "arctic jade korean maple": ("Acer pseudosieboldianum", "Arctic Jade"),
    "three flower maple": ("Acer triflorum", None),
    "northern flare maple": ("Acer", "Northern Flare"),
    # Fraxinus
    "patmore green ash": ("Fraxinus pennsylvanica", "Patmore"),
    "summit green ash": ("Fraxinus pennsylvanica", "Summit"),
    "bergeson green ash": ("Fraxinus pennsylvanica", "Bergeson"),
    "prairie spire ash": ("Fraxinus pennsylvanica", "Prairie Spire"),
    "mancana ash": ("Fraxinus mandshurica", "Mancana"),
    "fallgold black ash": ("Fraxinus nigra", "Fallgold"),
    "northern blaze white ash": ("Fraxinus americana", "Northern Blaze"),
    "skyline white ash": ("Fraxinus americana", "Skyline"),
    "northern treasure ash": ("Fraxinus", "Northern Treasure"),
    # Tilia
    "greenspire linden": ("Tilia cordata", "Greenspire"),
    "shamrock linden": ("Tilia cordata", "Shamrock"),
    "redmond linden": ("Tilia americana", "Redmond"),
    "boulevard linden": ("Tilia americana", "Boulevard"),
    "frontyard linden": ("Tilia americana", "Frontyard"),
    "american sentry linden": ("Tilia americana", "American Sentry"),
    "pyramidal american linden": ("Tilia americana", None),
    "harvest gold linden": ("Tilia", "Harvest Gold"),
    "norlin linden": ("Tilia", "Norlin"),
    "lighthouse linden": ("Tilia", "Lighthouse"),
    "lone star linden": ("Tilia", "Lone Star"),
    "sterling silver linden": ("Tilia tomentosa", "Sterling Silver"),
    "glenleven linden": ("Tilia x flavescens", "Glenleven"),
    # Ulmus
    "cathedral elm": ("Ulmus", "Cathedral"),
    "triumph elm": ("Ulmus", "Triumph"),
    "accolade elm": ("Ulmus", "Accolade"),
    "patriot elm": ("Ulmus", "Patriot"),
    "new horizon elm": ("Ulmus", "New Horizon"),
    "sapporo gold elm": ("Ulmus", "Sapporo Autumn Gold"),
    "frontier elm": ("Ulmus", "Frontier"),
    "discovery elm": ("Ulmus davidiana", "Discovery"),
    "princeton elm": ("Ulmus americana", "Princeton"),
    "prairie expedition elm": ("Ulmus americana", "Prairie Expedition"),
    "st. croix elm": ("Ulmus americana", "St. Croix"),
    "valley forge elm": ("Ulmus americana", "Valley Forge"),
    # Gleditsia, Gymnocladus
    "northern acclaim honeylocust": ("Gleditsia triacanthos", "Northern Acclaim"),
    "skyline honeylocust": ("Gleditsia triacanthos", "Skyline"),
    "prairie silk honeylocust": ("Gleditsia triacanthos", "Prairie Silk"),
    "shade tree master honeylocust": ("Gleditsia triacanthos", "Shademaster"),
    "espresso coffeetree": ("Gymnocladus dioicus", "Espresso"),
    "true north kentucky coffeetree": ("Gymnocladus dioicus", "True North"),
    "skinny latte kentucky coffee tree": ("Gymnocladus dioicus", "Skinny Latte"),
    # Aesculus
    "autumn splendor buckeye": ("Aesculus", "Autumn Splendor"),
    "prairie torch buckeye": ("Aesculus", "Prairie Torch"),
    "homestead buckeye": ("Aesculus", "Homestead"),
    "early glow ohio buckeye": ("Aesculus glabra", "Early Glow"),
    # Betula, Alnus
    "dakota pinnacle birch": ("Betula platyphylla", "Dakota Pinnacle"),
    "parkland pillar birch": ("Betula platyphylla", "Parkland Pillar"),
    "prairie dream birch": ("Betula papyrifera", "Prairie Dream"),
    "northern tribute birch": ("Betula", "Northern Tribute"),
    "royal frost birch": ("Betula", "Royal Frost"),
    "prairie horizon alder": ("Alnus hirsuta", "Prairie Horizon"),
    "spaeth's alder": ("Alnus", None),
    # Malus: ornamental crabapples are hybrids; the eating apples are domestica.
    "radiant crabapple": ("Malus", "Radiant"),
    "spring snow crabapple": ("Malus", "Spring Snow"),
    "thunderchild crabapple": ("Malus", "Thunderchild"),
    "indian magic crabapple": ("Malus", "Indian Magic"),
    "red splendor crabapple": ("Malus", "Red Splendor"),
    "adirondack crabapple": ("Malus", "Adirondack"),
    "prairie fire crabapple": ("Malus", "Prairiefire"),
    "chestnut crabapple": ("Malus", "Chestnut"),
    "royalty crabapple": ("Malus", "Royalty"),
    "cardinal crabapple": ("Malus", "Cardinal"),
    "donald wyman crabapple": ("Malus", "Donald Wyman"),
    "zestar apple": ("Malus domestica", "Zestar"),
    "kinderkrisp apple": ("Malus domestica", "KinderKrisp"),
    "haralson apple": ("Malus domestica", "Haralson"),
    "woodarz apple": ("Malus domestica", None),
    "sweet 16 apple": ("Malus domestica", "Sweet Sixteen"),
    "fireside apple": ("Malus domestica", "Fireside"),
    "red barron apple": ("Malus domestica", "Red Baron"),
    "hazen apple": ("Malus domestica", "Hazen"),
    "snowsweet apple": ("Malus domestica", "SnowSweet"),
    "honeycrisp apple": ("Malus domestica", "Honeycrisp"),
    # Prunus
    "canada red/shubert chokechery": ("Prunus virginiana", "Canada Red"),
    "amur chokecherry": ("Prunus maackii", None),
    "gold spur amur chokecherry": ("Prunus maackii", "Goldspur"),
    "gold rush amur chokecherry": ("Prunus maackii", "Goldrush"),
    "princess kay plum": ("Prunus nigra", "Princess Kay"),
    "mount royal plum": ("Prunus domestica", "Mount Royal"),
    "alderman plum": ("Prunus", "Alderman"),
    "pembina plum": ("Prunus", "Pembina"),
    "crimson pointe plum": ("Prunus", "Crimson Pointe"),
    "juliet cherry": ("Prunus", "Juliet"),
    "spring flair cherry": ("Prunus", "Spring Flair"),
    # Pyrus
    "prairie gem pear": ("Pyrus ussuriensis", "Prairie Gem"),
    "mountain frost pear": ("Pyrus ussuriensis", "Mountain Frost"),
    "korean sun pear": ("Pyrus fauriei", "Korean Sun"),
    "summer crisp pear": ("Pyrus", "Summercrisp"),
    "patten pear": ("Pyrus", "Patten"),
    # Sorbus
    "showy mountain ash": ("Sorbus decora", None),
    "korean mountain ash": ("Sorbus alnifolia", None),
    "black hawk mountain ash": ("Sorbus aucuparia", "Black Hawk"),
    "pyramidal mountain ash": ("Sorbus aucuparia", "Fastigiata"),
    "oak leaf mountain ash": ("Sorbus", None),
    "russian mountain ash": ("Sorbus", None),
    "red cascade mountain ash": ("Sorbus", "Red Cascade"),
    # Quercus
    "top gun bur oak": ("Quercus", "Top Gun"),
    "prairie stature oak": ("Quercus", "Prairie Stature"),
    "majestic skies northern pin oak": ("Quercus ellipsoidalis", "Majestic Skies"),
    "heritage oak": ("Quercus x macdanielii", "Heritage"),
    # Everything else
    "ivory silk japanese tree lilac": ("Syringa reticulata", "Ivory Silk"),
    "miss kim lilac": ("Syringa pubescens", "Miss Kim"),
    "summer flare lilac": ("Syringa", None),
    "autumn gold ginkgo": ("Ginkgo biloba", "Autumn Gold"),
    "autumn brilliance serviceberry": ("Amelanchier", "Autumn Brilliance"),
    "juneberry": ("Amelanchier", None),
    "snowbird hawthorn": ("Crataegus x mordenensis", "Snowbird"),
    "toba hawthorn": ("Crataegus x mordenensis", "Toba"),
    "meyer spruce": ("Picea meyeri", None),
    "medora juniper": ("Juniperus scopulorum", "Medora"),
    "white cedar": ("Thuja occidentalis", None),
    "his majesty amur corktree": ("Phellodendron amurense", "His Majesty"),
    "amur maacki": ("Maackia amurensis", None),
    "summertime amur maacki": ("Maackia amurensis", "Summertime"),
    "mn strain redbud": ("Cercis canadensis", None),
    "royal star magnolia": ("Magnolia stellata", "Royal Star"),
    "spring welcome magnolia": ("Magnolia", "Spring Welcome"),
    "snow mantle dogwood": ("Cornus", "Snow Mantle"),
    "laurel leaf willow": ("Salix pentandra", None),
    "prairie cascade willow": ("Salix", "Prairie Cascade"),
    "prairie reflection willow": ("Salix", "Prairie Reflection"),
    "flame willow": ("Salix", "Flame"),
    "swedish aspen": ("Populus tremula", "Erecta"),
    "siouxland poplar": ("Populus deltoides", "Siouxland"),
    "prairie sentinel hackberry": ("Celtis occidentalis", "Prairie Sentinel"),
    "northern advance plane tree": ("Platanus", "Northern Advance"),
    "honeyberry": ("Lonicera caerulea", None),
    "aronia berry": ("Aronia", None),
    "burgundy lace filbert": ("Corylus", "Burgundy Lace"),
    "barberry": ("Berberis", None),
    "ninebark": ("Physocarpus", None),
    "spirea": ("Spiraea", None),
    "potentilla": ("Potentilla", None),
    "euonymous": ("Euonymus", None),
    "cotoneaster": ("Cotoneaster", None),
}

# `species_genus` values that are not the genus as written.  None: not a genus
# (a family, a placeholder, a number).
GENUS_FIXES: dict[str, str | None] = {
    "quarcus": "Quercus",
    "aesculas": "Aesculus",
    "vibernum": "Viburnum",
    "magnolie": "Magnolia",
    "spirea": "Spiraea",
    "berberidinae": None,
    "betulaceae": None,
    "conifer": None,
    "stump": None,
    "n/a": None,
}


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("’", "'").split())
    return text or None


class Counts:
    status: Counter = Counter()
    archived = 0
    not_tree = 0
    empty = 0
    no_id = 0
    no_geometry = 0
    named = 0
    from_table = 0
    from_shared = 0
    from_genus: Counter = Counter()
    unresolved: Counter = Counter()


COUNTS = Counts()
SEEN_IDS: set[str] = set()


def keep(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    status = (clean(attrs.get("status")) or "").lower()
    if status not in TREE_STATUSES:
        if status not in NOT_TREE_STATUSES:
            raise RuntimeError(
                f"Moorhead ingest: unseen status {attrs.get('status')!r}; "
                "the TreePlotter status list has changed"
            )
        COUNTS.status[status] += 1
        return False
    if (clean(attrs.get("archived")) or "").lower() == "true":
        COUNTS.archived += 1
        return False
    common = (clean(attrs.get("species_common")) or "").lower()
    if common in NOT_TREES:
        COUNTS.not_tree += 1
        return False
    genus = (clean(attrs.get("species_genus")) or "").lower()
    if common in NO_NAME and genus in NO_NAME and not attrs.get("dbh_exact"):
        COUNTS.empty += 1
        return False
    guid = str(attrs.get("GlobalID") or "").strip().strip("{}").lower()
    if not guid:
        COUNTS.no_id += 1
        return False
    if guid in SEEN_IDS:
        raise RuntimeError(f"Moorhead ingest: GlobalID {guid} is repeated")
    SEEN_IDS.add(guid)
    lat, lon = esri_point(feature.get("geometry"))
    if lat is None or lon is None:
        COUNTS.no_geometry += 1
        return False
    return True


def genus_of(raw) -> str | None:
    text = clean(raw)
    if text is None:
        return None
    key = text.lower()
    if key in GENUS_FIXES:
        return GENUS_FIXES[key]
    if not text.isalpha():
        return None
    return text[:1].upper() + text[1:].lower()


def resolve(common_raw, genus_raw) -> tuple[str | None, str | None]:
    """(species, cultivar) from the row's common name and recorded genus.

    Examples:
        ("Emerald Lustre Norway Maple", "Acer") -> ("Acer platanoides", "Emerald Lustre")
        ("Green ash", "Fraxinus")               -> ("Fraxinus pennsylvanica", None)
        ("Buckeye", "Aesculas")                 -> ("Aesculus", None)
        ("", "Quarcus")                         -> ("Quercus", None)
    """
    common = clean(common_raw)
    if common and common.lower() not in NO_NAME:
        COUNTS.named += 1
        hit = CULTIVAR_NAMES.get(common.lower())
        if hit is not None:
            COUNTS.from_table += 1
            return hit
        species = species_from_common_name(common)
        if species:
            COUNTS.from_shared += 1
            return species, None
    genus = genus_of(genus_raw)
    if genus:
        COUNTS.from_genus[common or ""] += 1
        return genus, None
    if common and common.lower() not in NO_NAME:
        COUNTS.unresolved[common] += 1
    return None, None


def iter_chunks():
    yield from iter_features(
        LAYER,
        out_fields=OUT_FIELDS,
        return_geometry=True,
        out_sr=4326,
        order_by="OBJECTID",
    )


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    latitude: list[float] = []
    longitude: list[float] = []
    dbh: list[float | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        name, cv = resolve(attrs.get("species_common"), attrs.get("species_genus"))
        guid = str(attrs["GlobalID"]).strip().strip("{}").lower()
        raw_dbh = attrs.get("dbh_exact")
        tree_id.append(f"{ID_PREFIX}-{guid}")
        species.append(name)
        cultivar.append(cv)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(float(raw_dbh) if raw_dbh else None)

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["USMHD"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "cultivar": pa.array(cultivar, type=pa.string()),
        "plant_date": pa.array([None] * n, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


if __name__ == "__main__":
    table = stream_to_table(iter_chunks(), transform, keep=keep, label="Moorhead OpenData")
    print(
        f"Moorhead ingest: dropped by status {dict(COUNTS.status)}, {COUNTS.archived} archived, "
        f"{COUNTS.not_tree} park features (fields, trails), {COUNTS.empty} empty records, "
        f"{COUNTS.no_id} with no GlobalID, {COUNTS.no_geometry} with no geometry",
        file=sys.stderr,
    )
    print(
        f"Moorhead ingest: of {COUNTS.named:,} named rows, {COUNTS.from_table:,} read from the "
        f"cultivar table, {COUNTS.from_shared:,} from the shared English table, "
        f"{sum(COUNTS.from_genus.values()):,} (incl. unnamed) from the recorded genus; "
        f"genus fallbacks: {COUNTS.from_genus.most_common(15)}",
        file=sys.stderr,
    )
    unresolved = sum(COUNTS.unresolved.values())
    if unresolved:
        print(
            f"Moorhead ingest: {unresolved} named rows unresolved: "
            f"{COUNTS.unresolved.most_common(15)}",
            file=sys.stderr,
        )
    if COUNTS.named and unresolved / COUNTS.named > MAX_UNRESOLVED_SHARE:
        raise RuntimeError(
            f"Moorhead ingest: {unresolved} of {COUNTS.named} named rows resolve to no "
            "taxon; the inventory's naming has changed"
        )
    table = validate_coordinates(table, city="Moorhead", city_code="USMHD")
    table = enforce_tree_schema(
        table, city="Moorhead", data_source="MOORHEAD_OPENDATA"
    )
    emit(table)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Portland, Maine's City-owned trees, from the City's own ArcGIS Enterprise server.

LICENCE: no licence stated; displayed with attribution only, not licensed for
reuse.  The service's portal item (b12173632dfd448aa7a5bea80b6da47b on
https://gis.portlandmaine.gov/portal, owner `lizd@PORTLANDMAINE`, "City
Owned Trees", snippet "City tree layer maintained by Forestry") has empty
`licenseInfo` and `accessInformation`, the service publishes no copyright
text, and the City's portal states no terms of use.

Source: `ParksRec/Trees/MapServer/1` on gis.portlandmaine.gov (City of
Portland GIS, maintained by Parks, Recreation & Facilities' Forestry
division), the service's only layer: 21,688 rows on 2026-11-27, street,
park and cemetery trees (`Growspace` Lawn, Tree Lawn, Park, Tree Well,
Cemetery, ...).  A versioned enterprise geodatabase edited in place
(OBJECTID 1..154,787 with gaps, `EditDate` to 2024-09-19); the same layer is
also served as `FeatureServer/1`.  Geometry is stored in Maine State Plane
West feet (EPSG 26856) and read in WGS84 (`outSR=4326`).

**The id is the GlobalID** (`portlandme-{guid}`), non-null and distinct on
all 21,688 rows (checked 2026-11-27); a repeated GlobalID refuses the build.
`FacilityID` (`00001`...) is unique too but three rows have none, and
`Old_Facility_ID` / `Old_ObjectID` repeat.

**Which rows are trees.**  `TreeStatus` mixes condition (Good, Fair,
Poor/Remove) with site states: `Stump`, `Empty Tree Well` and `Planned
Planting` are dropped.  `Other` (1,466 rows) and an empty status are the
inventory's review bucket: most carry a comment saying the tree is gone
("Removed when?", "No tree", "Duplicate", "Stump removed 2012") and those
are dropped; the rest (park trees with no condition yet, private trees)
stay.  A `BotanicalName` of `STUMP` or a `CommonName` of `Stump`, `NO
TREE` or `EMPTY` is dropped wherever it appears.  `Maintenance = Remove`
trees are still standing and stay.

**Species is `BotanicalName`**, a coded-value field read through its domain
(`Acer platanoides CK` is `Acer platanoides 'Crimson King'`, `Acer
grisuem` is `Acer griseum`), with this inventory's unquoted cultivars and
non-taxon forms rewritten in `BOTANICAL_REWRITES` (`Prunus Accolade`,
`Ginkgo biloba M`, `Magnoliaceae`, the genus typo `Mallus`), then the
shared sanitiser, whose misspelling map folds the rest (`Taxxus baccata`).  2,623 rows
have no botanical name (and 703 say `Unknown`); for those, and where the
botanical name is genus-only and the common name names a species of that
genus, the `CommonName` domain value -- an inverted English name, `Maple
Red`, `Oak Swamp White` -- is resolved through `LOCAL_COMMON_NAMES` (this
inventory's inverted forms, each checked against the rows where both
columns are filled) and then the shared English table.  The common name is
never published.  A botanical value that does not read as a taxon counts
as unreadable; above 1% the build is refused.

**DBH is `DBH`, whole inches** (median 11, 99th percentile 44, max 91);
0 and the `-1` placeholder are nulled by the schema.  `TrunkNum` is a
stem-count class and is not read.

**`PlantingDate`** is set on ~1,100 rows (mostly 2013-2022, a few labelled
1976 and 1994 plantings); stored as UTC midnight or Eastern midnight
(04:00 UTC), so the UTC calendar date is the date entered.  The one
1970-01-01 (Eastern midnight of the epoch) is a placeholder and is nulled.
No height or crown spread is recorded.
"""

import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (  # noqa: E402
    FeatureLayer,
    coded_value_domain,
    esri_ms_to_date,
    esri_point,
    iter_features,
)
from shared.ingest import (  # noqa: E402
    UNKNOWN_SPECIES,
    emit,
    enforce_tree_schema,
    sanitize_species,
    stream_to_table,
    validate_coordinates,
)
from shared.species.english import species_from_common_name  # noqa: E402

SOURCE_URL = "https://gis.portlandmaine.gov/maps/rest/services/ParksRec/Trees/MapServer/1"
LAYER = FeatureLayer(SOURCE_URL, timeout=180)
OUT_FIELDS = (
    "OBJECTID,GlobalID,BotanicalName,CommonName,DBH,TreeStatus,Comments,"
    "TreeNotes,PlantingDate"
)
ID_PREFIX = "portlandme"

# `TreeStatus` values that are a site, not a tree.
NOT_TREE_STATUS = frozenset({"Stump", "Empty Tree Well", "Planned Planting"})
# Statuses that are a review bucket rather than a condition; a row in one is
# dropped when its comment or note says the tree is gone.
REVIEW_STATUS = frozenset({"Other", None})
GONE = re.compile(
    r"remov|no tree|nothing here|not here|not present|\bgone\b|duplicate|"
    r"\bground\b|grind|stump|taken out|killed|bricked|asphalt|open hole|existed when",
    re.IGNORECASE,
)
NOT_TREE_BOTANICAL = frozenset({"stump"})
NOT_TREE_COMMON = frozenset({"stump", "no tree", "empty"})
UNKNOWN_VALUES = frozenset({"unknown"})

# Domain display names (after whitespace clean-up) whose shape the shared
# sanitiser would misread: unquoted cultivars, sex marks, a family.
BOTANICAL_REWRITES = {
    # Typos `species_audit.py` does not pair (a bare genus; a name no other
    # city spells this way), so the shared misspelling map cannot carry them.
    "Mallus": "Malus",  # paired with common name `Crabapple` / `Apple` on 1,039 of 1,042 rows
    "Salix negra": "Salix nigra",
    "Prunus Accolade": "Prunus 'Accolade'",
    "Malus x Adirondack": "Malus 'Adirondack'",
    "Malus x species": "Malus",
    "Magnolia Elizabeth": "Magnolia 'Elizabeth'",
    "Magnolia x hybrid": "Magnolia",
    "Magnoliaceae": "Magnolia",  # paired with common name `Magnolia` on 62 of 66 rows
    "Zelkova Mussahino": "Zelkova serrata 'Musashino'",
    "Stewartia Korean": "Stewartia pseudocamellia",
    "Ginkgo biloba M": "Ginkgo biloba",
    "Ginkgo biloba F": "Ginkgo biloba",
    "Ulmus americana Princeton": "Ulmus americana 'Princeton'",
    "Acer rubrum Karpick": "Acer rubrum 'Karpick'",
    "Pyrus calleryana x": "Pyrus calleryana",
    "Ulmus americana x": "Ulmus americana",
    "Populus x": "Populus",
    "Pinus x": "Pinus",
}

# The `CommonName` domain's inverted English names the shared table does not
# resolve (genus noun first: `Maple Red`), each checked against the rows
# that carry both columns.  Keys are the domain display names.
LOCAL_COMMON_NAMES = {
    "Alder Black": "Alnus glutinosa",
    "Amelanchier": "Amelanchier",
    "Arborvitae Eastern": "Thuja occidentalis",
    "Ash Autumn Purple": "Fraxinus americana 'Autumn Purple'",
    "Ash European": "Fraxinus excelsior",
    "Ash European Mountain": "Sorbus aucuparia",
    "Ash Green": "Fraxinus pennsylvanica",
    "Ash Green Leprechaun": "Fraxinus pennsylvanica 'Leprechaun'",
    "Ash White": "Fraxinus americana",
    "Beech American": "Fagus grandifolia",
    "Beech European": "Fagus sylvatica",
    "Beech Europen Upright": "Fagus sylvatica",
    "Birch Gray": "Betula populifolia",
    "Birch River": "Betula nigra",
    "Birch White": "Betula papyrifera",
    "Birch White European": "Betula pendula",
    "Birch Yellow": "Betula alleghaniensis",
    "Cedar": None,  # paired with Cedrus on 39 rows; the shared table says Thuja
    "Cedar Atlantic White": "Chamaecyparis thyoides",
    "Cedar Eastern Red": "Juniperus virginiana",
    "Cedar Red Juniper": "Juniperus virginiana",
    "Cherry Accolade": "Prunus 'Accolade'",
    "Cherry Black": "Prunus serotina",
    "Cherry Cornelian": "Cornus mas",
    "Cherry Pink Flair": "Prunus sargentii 'Pink Flair'",
    "Cherry Sargent Accolade": "Prunus 'Accolade'",
    "Dogwood Florida": "Cornus florida",
    "Dogwood Kousa": "Cornus kousa",
    "Elm American": "Ulmus americana",
    "Elm American Princeton": "Ulmus americana 'Princeton'",
    "Elm Camperdown": "Ulmus glabra 'Camperdownii'",
    "Elm Lacebark": "Ulmus parvifolia",
    "Elm Siberian": "Ulmus pumila",
    "Euonymous European": "Euonymus europaeus",
    "European Spindle": "Euonymus europaeus",
    "Filbert Turkish": "Corylus colurna",
    "Fir Concolor": "Abies concolor",
    "Ginkgo Female": "Ginkgo biloba",
    "Ginkgo Male": "Ginkgo biloba",
    "Hawthorn Crusgali": "Crataegus crus-galli",
    "Hawthorn Winter King": "Crataegus viridis 'Winter King'",
    "Hemlock Canadian": "Tsuga canadensis",
    "Hemlock Carolina": "Tsuga caroliniana",
    "Hickory Pignut": "Carya glabra",
    "Hickory Shagbark": "Carya ovata",
    "Hornbeam American": "Carpinus caroliniana",
    "Hornbeam European": "Carpinus betulus",
    "Hornbeam Hop": "Ostrya virginiana",
    "Juniper Red Cedar": "Juniperus virginiana",
    "Larch Eastern": "Larix laricina",
    "Larch European": "Larix decidua",
    "Larch Japanese": "Larix kaempferi",
    "Lilac Common": "Syringa vulgaris",
    "Lilac Ivory Silk": "Syringa reticulata 'Ivory Silk'",
    "Lilac Japanese": "Syringa reticulata",
    "Linden Littleleaf European": "Tilia cordata",
    "Locust Thornless Honey": "Gleditsia triacanthos",
    "Magnolia Cucumber": "Magnolia acuminata",
    "Maple Amur": "Acer ginnala",
    "Maple Armstrong Red": "Acer rubrum 'Armstrong'",
    "Maple Celebration": "Acer x freemanii 'Celebration'",
    "Maple Hedge": "Acer campestre",
    "Maple Japanese": "Acer palmatum",
    "Maple Korean": "Acer pseudosieboldianum",
    "Maple Norway": "Acer platanoides",
    "Maple Norway Crimson King": "Acer platanoides 'Crimson King'",
    "Maple Paperbark": "Acer griseum",
    "Maple Red": "Acer rubrum",
    "Maple Red Autumn Blaze": "Acer x freemanii 'Autumn Blaze'",
    "Maple Red Karpick": "Acer rubrum 'Karpick'",
    "Maple Rocky Mountain": "Acer glabrum",
    "Maple Silver": "Acer saccharinum",
    "Maple Striped": "Acer pensylvanicum",
    "Maple Sugar": "Acer saccharum",
    "Maple Sugar Dwarf": "Acer saccharum",
    "Maple Sycamore": "Acer pseudoplatanus",
    "Maple Three Flower": "Acer triflorum",
    "Mountain Ash American": "Sorbus americana",
    "Mountain Ash European": "Sorbus aucuparia",
    "Mountain Ash Korean": "Sorbus alnifolia",
    "Oak Black": "Quercus velutina",
    "Oak Bur": "Quercus macrocarpa",
    "Oak Chestnut": "Quercus montana",
    "Oak English": "Quercus robur",
    "Oak English upright": "Quercus robur 'Fastigiata'",
    "Oak Pin": "Quercus palustris",
    "Oak Pin Upright": "Quercus palustris",
    "Oak Red": "Quercus rubra",
    "Oak Swamp White": "Quercus bicolor",
    "Oak White": "Quercus alba",
    "Olive Autumn": "Elaeagnus umbellata",
    "Pagoda": "Styphnolobium japonicum",
    "Pear Callery": "Pyrus calleryana",
    "Pine Austrian": "Pinus nigra",
    "Pine Jeffrey": "Pinus jeffreyi",
    "Pine Mugo": "Pinus mugo",
    "Pine Pitch": "Pinus rigida",
    "Pine Red": "Pinus resinosa",
    "Pine Scotch": "Pinus sylvestris",
    "Pine Swiss Stone": "Pinus cembra",
    "Pine White": "Pinus strobus",
    "Pine White Eastern": "Pinus strobus",
    "Planetree London": "Platanus x hispanica",
    "Plum Newport Cherry": "Prunus cerasifera 'Newport'",
    "Plum Thundercloud": "Prunus cerasifera 'Thundercloud'",
    "Redbud Eastern": "Cercis canadensis",
    "Redwood Dawn": "Metasequoia glyptostroboides",
    "Smokebush American": "Cotinus obovatus",
    "Spruce Blue": "Picea pungens",
    "Spruce Norway": "Picea abies",
    "Spruce Red": "Picea rubens",
    "Spruce Serbian": "Picea omorika",
    "Spruce White": "Picea glauca",
    "Stewartia": "Stewartia",
    "Tupelo": "Nyssa sylvatica",
    "Walnut Black": "Juglans nigra",
    "Willow Black": "Salix nigra",
    "Willow Weeping": "Salix babylonica",
    "Zelkova Mussahino": "Zelkova serrata 'Musashino'",
}

MAX_UNPARSED_SHARE = 0.01


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("\xa0", " ").split())
    return text or None


class Counts:
    no_id = 0
    no_geometry = 0
    dropped: Counter = Counter()
    unparsed: Counter = Counter()
    named = 0
    source: Counter = Counter()
    placeholder_dates = 0


COUNTS = Counts()
_DOMAINS: dict[str, dict[str, str]] = {}
_SEEN_IDS: set[str] = set()


def domains() -> dict[str, dict[str, str]]:
    if not _DOMAINS:
        _DOMAINS["BotanicalName"] = coded_value_domain(LAYER, "BotanicalName")
        _DOMAINS["CommonName"] = coded_value_domain(LAYER, "CommonName")
    return _DOMAINS


def decoded(field: str, code) -> str | None:
    text = clean(code)
    if text is None:
        return None
    return clean(domains()[field].get(str(code), text))


def guid_of(attrs: dict) -> str | None:
    guid = str(attrs.get("GlobalID") or "").strip().strip("{}").lower()
    return guid or None


def keep(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    status = clean(attrs.get("TreeStatus"))
    botanical = (decoded("BotanicalName", attrs.get("BotanicalName")) or "").lower()
    common = (decoded("CommonName", attrs.get("CommonName")) or "").lower()
    if status in NOT_TREE_STATUS:
        COUNTS.dropped[f"status {status}"] += 1
        return False
    if botanical in NOT_TREE_BOTANICAL or common in NOT_TREE_COMMON:
        COUNTS.dropped[f"species {botanical or common}"] += 1
        return False
    if status in REVIEW_STATUS:
        note = f"{attrs.get('Comments') or ''} {attrs.get('TreeNotes') or ''}"
        if GONE.search(note):
            COUNTS.dropped[f"status {status or 'empty'}, comment says gone"] += 1
            return False
    guid = guid_of(attrs)
    if guid is None:
        COUNTS.no_id += 1
        return False
    if guid in _SEEN_IDS:
        raise RuntimeError(f"Portland, ME ingest: GlobalID {guid} is repeated")
    _SEEN_IDS.add(guid)
    lat, lon = esri_point(feature.get("geometry"))
    if lat is None or lon is None:
        COUNTS.no_geometry += 1
        return False
    return True


def resolve_common(common: str | None) -> str | None:
    if common is None:
        return None
    if common in LOCAL_COMMON_NAMES:
        return LOCAL_COMMON_NAMES[common]
    resolved = species_from_common_name(common)
    return resolved if resolved and sanitize_species(resolved) else None


def parse_species(botanical_code, common_code) -> str | None:
    """The species value for the shared sanitiser.

    Examples:
        "Acer platanoides CK", None      -> "Acer platanoides 'Crimson King'"
        "Prunus Accolade", None          -> "Prunus 'Accolade'"
        None, "Maple Red"                -> "Acer rubrum"
        "Unknown", "Oak Red"             -> "Quercus rubra"
        "Tilia", "Linden Littleleaf European" -> "Tilia cordata"
        "Unknown", "Unknown"             -> "Unknown"
        None, None                       -> None
    """
    botanical = decoded("BotanicalName", botanical_code)
    common_value = decoded("CommonName", common_code)
    common = resolve_common(common_value)
    if botanical is not None:
        COUNTS.named += 1
        botanical = BOTANICAL_REWRITES.get(botanical, botanical)
    taxon = sanitize_species(botanical) if botanical else None
    if botanical is not None and taxon is None and botanical.lower() not in UNKNOWN_VALUES:
        COUNTS.unparsed[botanical] += 1
    if taxon is None:
        if common is not None:
            COUNTS.source["common name (no botanical name)"] += 1
            return common
        if botanical is not None or common_value is not None:
            COUNTS.source["unknown"] += 1
            return UNKNOWN_SPECIES
        COUNTS.source["no species"] += 1
        return None
    if " " not in taxon and common is not None:
        finer = sanitize_species(common)
        if finer and " " in finer and finer.split()[0] == taxon:
            COUNTS.source["common name refines genus"] += 1
            return common
    COUNTS.source["botanical name"] += 1
    return botanical


def plant_date(value) -> date | None:
    day = esri_ms_to_date(value)
    if day == date(1970, 1, 1):
        COUNTS.placeholder_dates += 1
        return None
    return day


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
    planted: list[date | None] = []
    latitude: list[float] = []
    longitude: list[float] = []
    dbh: list[float | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        tree_id.append(f"{ID_PREFIX}-{guid_of(attrs)}")
        species.append(parse_species(attrs.get("BotanicalName"), attrs.get("CommonName")))
        planted.append(plant_date(attrs.get("PlantingDate")))
        latitude.append(lat)
        longitude.append(lon)
        diameter = attrs.get("DBH")
        dbh.append(float(diameter) if diameter is not None else None)

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["USPWM"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "plant_date": pa.array(planted, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


if __name__ == "__main__":
    domains()
    table = stream_to_table(iter_chunks(), transform, keep=keep, label="Portland, ME OpenData")
    dropped = ", ".join(f"{n:,} {why}" for why, n in COUNTS.dropped.most_common())
    print(
        f"Portland, ME ingest: dropped {sum(COUNTS.dropped.values()):,} rows that are "
        f"not trees ({dropped}), {COUNTS.no_id} with no GlobalID, "
        f"{COUNTS.no_geometry} with no geometry; {COUNTS.placeholder_dates} "
        f"placeholder plant dates nulled; species from {dict(COUNTS.source)}",
        file=sys.stderr,
    )
    unparsed = sum(COUNTS.unparsed.values())
    if unparsed:
        print(
            f"Portland, ME ingest: {unparsed} botanical names unreadable: "
            f"{COUNTS.unparsed.most_common(10)}",
            file=sys.stderr,
        )
    if COUNTS.named and unparsed / COUNTS.named > MAX_UNPARSED_SHARE:
        raise RuntimeError(
            f"Portland, ME ingest: {unparsed} of {COUNTS.named} botanical names "
            "unreadable; the BotanicalName domain's format has changed"
        )
    table = validate_coordinates(table, city="Portland, ME", city_code="USPWM")
    table = enforce_tree_schema(
        table, city="Portland, ME", data_source="PORTLAND_ME_OPENDATA"
    )
    emit(table)

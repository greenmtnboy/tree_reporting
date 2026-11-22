#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Sioux Falls, South Dakota's City tree inventory.

Source: "Trees" (ArcGIS item c880d62ae5fb4652b1f8e6cbca244107, owner
`cityofsiouxfallsgis`, "Feature layer containing authoritative tree points for
Sioux Falls, South Dakota"), the City's own map service
`gis.siouxfalls.gov/arcgis/rest/services/Data/Trees/MapServer/10`: 85,591 rows
on 2026-11-22, an enterprise geodatabase the Forestry division edits in place
(`last_edited_date` moves daily).  The Park service's `Data/Park/MapServer/1`
is the same table under another name ("Inventory of trees in public
right-of-way ... or on City-owned property", same fields, 85,585 rows) and is
not read.  Stored in EPSG:32164 and read reprojected to WGS84 (`outSR=4326`).

Licence: the item's licence field reads "The City of Sioux Falls provides open
data licensed under the Creative Commons Attribution 4.0 International
License" (https://www.arcgis.com/home/item.html?id=c880d62ae5fb4652b1f8e6cbca244107).
Attribute to the City of Sioux Falls.

**Which rows are trees.**  `SiteType` (a coded-value domain) records a tree,
a planting site, a stump or a removal site.  Kept: `Tree`, `Treated` (ash on
EAB treatment), `Needs Inspection`, `Planned EAB Removal`, `Marked for
Removal`, `Unapproved Tree` (planted in the right-of-way without a permit),
`Memorial Tree` and `Homeowner Active or Planned Treatment` -- standing trees
whatever is planned for them.  Dropped with a logged count: `Removal site`
(28,585 in Nov 2026, the trees the EAB programme has taken down), `Planting
site`, `Available Planting Site`, `Approved` (an approved planting permit,
never a species), `CommuniTree` (planting-programme points, no species),
`Stump`, `Landscaping Debris Can't Grind Stump`, `Out of Compliance` (a
permit-compliance flag whose rows mix removed trees and empty sites) and the
handful with no `SiteType`.  An unseen `SiteType` refuses the build.  A kept
tree may carry a `RemovalYear`: it is the site's, kept when a new tree goes in
where an old one came out.

**The id is the GlobalID** (`siouxfalls-{guid}`), non-null on every row; the
City's `TreeID` is null on 15,580 rows and `SiteID` on 71,634.

**Species is `Species`, the scientific name** (title-cased: `Acer
Platanoides 'Crimson King'`); `Genus` is its own column.  `Species` wins when
the two disagree (a few hundred rows, e.g. Genus `Pinus` on `Picea Glauca Var.
Densata`); a `Species` that is only an epithet (`occidentalis`, `Americana`)
is read with `Genus`; a row with no `Species` takes the genus alone.  The
field is 30 characters, so cultivars are often abbreviated or cut short, and
written glued to the name (`Malus'Prairie Fire'`) or unquoted after it
(`Acer Platanoides Crimson Kng`, `Gleditsia Triacanthos Shadem`):
`CULTIVAR_FIXES` restores the full names, and `SPECIES_FIXES` reads the
values the general rule cannot (hybrid elms written as crosses, `U. Jap X Wil
'Morton'`; `Fraxinus Pennsylvanica Marsh`, which the layer's own `FullName`
pairs with "'Marshall Seedless' Green Ash").  Single misspelt words are left
to the shared misspelling map.  About 8,000 kept trees have neither `Genus` nor
`Species`, only a common name: those resolve through the shared English
table (`shared.species.english`), `FullName` first and then the abbreviated
`CommonName`; a common name the table does not know publishes Unknown
(Barrie's rule).  Where a Latin name is present it wins and the common name
is not read.  A parse-failure guard refuses more than 1%
unreadable values.

**DBH is `Diameter`, inches** (kept trees: median ~10, 99th percentile ~35);
`0` is the unmeasured placeholder and is nulled.  **`Spread` is the crown
width in feet**, converted to metres, `0` nulled.  `Height` (feet) has no
canonical column.

**Planting date** is `InstallYear` (a year domain), as 1 January of that
year.  2019 is the year of the citywide inventory, stamped on 29,220 rows
whatever their age (a 44-inch ash, a 39-inch silver maple), and is nulled;
so are years after the current one (the domain runs to 2030) and before the
City's founding (one row of 1851).
"""

import re
import sys
from collections import Counter
from datetime import date
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
    "https://gis.siouxfalls.gov/arcgis/rest/services/Data/Trees/MapServer/10",
    timeout=180,
)
OUT_FIELDS = (
    "OBJECTID,GlobalID,SiteType,Genus,Species,FullName,CommonName,"
    "Diameter,Spread,InstallYear"
)
ID_PREFIX = "siouxfalls"

TREE_SITE_TYPES = frozenset({
    "tree",
    "treated",
    "needs inspection",
    "planned eab removal",
    "marked for removal",
    "unapproved tree",
    "memorial tree",
    "homeowner active or planned treatment",
})
NOT_A_TREE_SITE_TYPES = frozenset({
    "removal site",
    "planting site",
    "available planting site",
    "approved",
    "communitree",
    "stump",
    "landscaping debris can't grind stump",
    "out of compliance",
})

# The citywide inventory's year, stamped as the install year on every tree it
# recorded; see the module docstring.
INVENTORY_YEAR = 2019
# Sioux Falls was founded in 1856.
EARLIEST_PLANT_YEAR = 1856

FEET_TO_M = 0.3048

# Refuse to publish when more than this share of named rows cannot be read.
MAX_UNPARSED_SHARE = 0.01

# `Species` values (lower-cased, whitespace collapsed) the general reading
# gets wrong, mapped to `Taxon 'Cultivar'` (or the taxon alone).
SPECIES_FIXES: dict[str, str | None] = {
    # Hybrid elms written as their crosses.  'New Horizon' is pumila x
    # japonica, the Morton series japonica x wilsoniana (or pumila); the
    # layer's FullName names each.
    "u. pumila x jap 'new horizon'": "Ulmus 'New Horizon'",
    "newhorizonelm": "Ulmus 'New Horizon'",
    "u. jap x wil 'morton'": "Ulmus 'Morton'",
    "ulmus 'morton' accolade": "Ulmus 'Morton'",
    "u. jap x wil 'morton red tip'": "Ulmus 'Morton Red Tip'",
    "u. jap x pumila 'morton plains": "Ulmus 'Morton Plainsman'",
    "ulmus x frontier": "Ulmus 'Frontier'",
    "u. davidiana var japonica": "Ulmus davidiana var. japonica",
    "ulmus davidiana car. japonica 'burgundy glow'": "Ulmus davidiana var. japonica 'Burgundy Glow'",
    # Heritage oak, bur x English.
    "q. robur x macrocarpa 'clemons": "Quercus x macdanielii 'Clemons'",
    "a. x arnoldiana'autmn splendor": "Aesculus x arnoldiana 'Autumn Splendor'",
    # Manchurian x black ash ('Northern Gem' and kin): the genus.
    "fraxinus manchurian x nigra": "Fraxinus",
    # Abbreviated cultivars of green ash, read through the layer's FullName
    # ("'Marshall Seedless' Green Ash", "Prairie Spire Green Ash").
    "fraxinus pennsylvanica marsh": "Fraxinus pennsylvanica 'Marshall'",
    "fraxinus pennsylvanica prair": "Fraxinus pennsylvanica 'Prairie Spire'",
    # "Apple" / "Crabapple, Species" in FullName: the genus.
    "malus fruit": "Malus",
    # FullName "Silver Maple Hybrid", FullName "Hybrid Poplar": the genus.
    "acer saccharium hybrid": "Acer",
    "populus hybrid": "Populus",
    "crataegus crus-galli inermis": "Crataegus crus-galli var. inermis",
    "picea glauca var. densata": "Picea glauca var. densata",
    "sylvvestris 'mongolian'": "Pinus sylvestris var. mongolica",
    # Picea pungens 'Hoopsii', written under the pine genus.
    "pinus pungens'hoopsii'": "Picea pungens 'Hoopsii'",
    "titia american'mcksentry'": "Tilia americana 'McKSentry'",
    "ulmus american'valley forge'": "Ulmus americana 'Valley Forge'",
    "tilia americana fastigata": "Tilia americana 'Fastigiata'",
    "acer platanoides variegated": "Acer platanoides",
    "magnolia kobus var sellata'roy": "Magnolia kobus var. stellata 'Royal Star'",
    # Garbles too short or too far for the shared misspelling audit: the
    # Freeman maple 'Bailston' (Matador), the Harbin alder (written in full
    # on the layer's other Alnus hirsuta rows), and black maple.
    "acer freemanni bailson": "Acer x freemanii 'Bailston'",
    "alnus histora": "Alnus hirsuta",
    "acer nigra": "Acer nigrum",
}

# `Genus` values misspelt in the domain, read only when `Species` is empty.
GENUS_FIXES: dict[str, str] = {
    "gingko": "Ginkgo",
    "continus": "Cotinus",
    "gleditsea": "Gleditsia",
}

# A (Genus, epithet-only Species) pair that is not a taxon: the genus alone.
EPITHET_FIXES: dict[tuple[str, str], str] = {
    ("quercus", "americana"): "Quercus",
}

# Cultivar text as the 30-character field abbreviates or cuts it (lower-cased)
# -> the cultivar's name.
CULTIVAR_FIXES: dict[str, str | None] = {
    "crimson kng": "Crimson King",
    "shadem": "Shademaster",
    "sty manor": "Stately Manor",
    "emrld queen": "Emerald Queen",
    "emrld lustr": "Emerald Lustre",
    "green mountn": "Green Mountain",
    "grn mtn": "Green Mountain",
    "pra senti": "Prairie Sentinel",
    "p robe": "Purple Robe",
    "his maj": "His Majesty",
    "princeton gld": "Princeton Gold",
    "silvr queen": "Silver Queen",
    "summt": "Summit",
    "state st": "State Street",
    "espresso-j": "Espresso-JFS",
    "chgoland": "Chicagoland",
    "swtshd": "Sweetshade",
    "mor cir": "Morton Circle",
    "bloodgd": "Bloodgood",
    "silver sterlin": "Sterling",
    "blvd": "Boulevard",
    "swifts silver": "Swift's Silver",
    "noreaster": "Noreaster",
}

# Words after the name that are an infraspecific rank (the rest is botanical).
RANK_WORDS = frozenset({"var", "var.", "subsp", "subsp.", "ssp", "ssp.", "f.", "cv.", "cv"})
HYBRID_MARKS = frozenset({"x", "×"})


def clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).replace("‘", "'").replace("’", "'")
    text = " ".join(text.split())
    return text or None


def _genus(raw) -> str | None:
    text = clean(raw)
    if text is None or text == "999" or not text.isalpha():
        return None
    return GENUS_FIXES.get(text.lower(), text[:1].upper() + text[1:].lower())


def _cultivar(text: str) -> str | None:
    text = " ".join(text.replace("'", " ").split())
    if not text:
        return None
    fixed = CULTIVAR_FIXES.get(text.lower(), text)
    if fixed is None:
        return None
    if fixed == fixed.lower():
        fixed = " ".join(w[:1].upper() + w[1:] for w in fixed.split())
    return fixed


def _from_fix(fixed: str | None) -> tuple[str | None, str | None]:
    """A `SPECIES_FIXES` value, `Taxon 'Cultivar'`, as (taxon, cultivar)."""
    if fixed is None:
        return None, None
    taxon, quote, cultivar = fixed.partition(" '")
    return taxon, (cultivar.rstrip("'") if quote else None)


def parse_species(raw_species, raw_genus) -> tuple[str | None, str | None, bool]:
    """(taxon, cultivar, whether the value failed to parse).

    The cultivar is returned as its own column rather than quoted into the
    species string, so a name holding an apostrophe ('Swift's Silver')
    survives the schema's split.

    Examples:
        "Acer Platanoides 'Crimson King'"  -> ("Acer Platanoides", "Crimson King")
        "Malus'Prairie Fire'"              -> ("Malus", "Prairie Fire")
        "Acer Platanoides Crimson Kng"     -> ("Acer Platanoides", "Crimson King")
        "Gymnocladus Dioicus'Espresso-J"   -> ("Gymnocladus Dioicus", "Espresso-JFS")
        "Aesculus X'Homestead'"            -> ("Aesculus", "Homestead")
        "occidentalis" (Genus Celtis)      -> ("Celtis occidentalis", None)
        None (Genus Quercus)               -> ("Quercus", None)
    """
    genus = _genus(raw_genus)
    text = clean(raw_species)
    if text is None or text == "999":
        return genus, None, False
    if text.lower() in SPECIES_FIXES:
        return (*_from_fix(SPECIES_FIXES[text.lower()]), False)

    head, quote, tail = text.partition("'")
    # A quoted cultivar, possibly glued to the name and possibly cut short
    # by the field's width (no closing quote).
    cultivar = _cultivar(tail.split("'", 1)[0]) if quote else None
    words = head.split()
    if words and words[-1].lower() in HYBRID_MARKS:
        # `Aesculus X'Homestead'`: a hybrid mark before a bare cultivar.
        words = words[:-1]
    if not words or not all(w.replace("-", "").replace(".", "").isalpha() for w in words):
        return None, None, True
    if len(words) == 1:
        word = words[0]
        if genus and word.lower() != genus.lower():
            # An epithet alone, read with the Genus column.
            pair = EPITHET_FIXES.get((genus.lower(), word.lower()))
            if pair is not None:
                return pair, None, False
            return f"{genus} {word.lower()}", cultivar, False
        return word, cultivar, False

    # Genus, an optional hybrid mark, the epithet, then an optional rank and
    # its name; any other words are an unquoted cultivar.
    n = 3 if words[1].lower() in HYBRID_MARKS and len(words) > 2 else 2
    taxon = words[:n]
    rest = words[n:]
    if rest and rest[0].lower() in RANK_WORDS:
        if rest[0].lower().startswith("cv"):
            rest = rest[1:]
        else:
            taxon += rest[:2]
            rest = rest[2:]
    if rest:
        if cultivar is not None:
            return None, None, True
        cultivar = _cultivar(" ".join(rest))
    return " ".join(taxon), cultivar, False


class Counts:
    no_geometry = 0
    no_id = 0
    not_a_tree: Counter = Counter()
    unparsed: Counter = Counter()
    named = 0
    from_common = 0
    unresolved_common: Counter = Counter()
    placeholder_year = 0
    out_of_range_year = 0


COUNTS = Counts()


def keep(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    site = (clean(attrs.get("SiteType")) or "").lower()
    if site not in TREE_SITE_TYPES:
        if site and site not in NOT_A_TREE_SITE_TYPES:
            raise RuntimeError(
                f"Sioux Falls ingest: unseen SiteType {attrs.get('SiteType')!r}; "
                "decide whether it is a standing tree"
            )
        COUNTS.not_a_tree[site or "(none)"] += 1
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
    if raw in (None, "", 0):
        return None
    year = int(raw)
    if year == INVENTORY_YEAR:
        COUNTS.placeholder_year += 1
        return None
    if year < EARLIEST_PLANT_YEAR or year > date.today().year:
        COUNTS.out_of_range_year += 1
        return None
    return date(year, 1, 1)


def positive(raw) -> float | None:
    if raw is None:
        return None
    value = float(raw)
    return value if value > 0 else None


def common_name_species(attrs: dict) -> str | None:
    """The species of a row with no Latin name, from its common name.

    `FullName` first (`Green Ash`, `New Horizon Elm`), then the abbreviated
    `CommonName` (`Ash,Green`), through the shared English table; a name the
    table does not know publishes Unknown (Barrie's rule).
    """
    names = [clean(attrs.get("FullName")), clean(attrs.get("CommonName"))]
    names = [n for n in names if n and n != "999"]
    for name in names:
        species = species_from_common_name(name)
        if species:
            COUNTS.from_common += 1
            return species
    if names:
        COUNTS.unresolved_common[names[0]] += 1
    return None


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
    planted: list[date | None] = []
    latitude: list[float] = []
    longitude: list[float] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        name, cv, failed = parse_species(attrs.get("Species"), attrs.get("Genus"))
        if name is None and not failed:
            name = common_name_species(attrs)
        if clean(attrs.get("Species")) or _genus(attrs.get("Genus")):
            COUNTS.named += 1
        if failed:
            COUNTS.unparsed[clean(attrs.get("Species"))] += 1
        guid = str(attrs["GlobalID"]).strip().strip("{}").lower()
        tree_id.append(f"{ID_PREFIX}-{guid}")
        species.append(name)
        cultivar.append(cv)
        planted.append(plant_date(attrs.get("InstallYear")))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(positive(attrs.get("Diameter")))
        spread = positive(attrs.get("Spread"))
        crown.append(spread * FEET_TO_M if spread is not None else None)

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["USFSD"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "cultivar": pa.array(cultivar, type=pa.string()),
        "plant_date": pa.array(planted, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        "crown_width_m": pa.array(crown, type=pa.float64()),
    })


if __name__ == "__main__":
    table = stream_to_table(iter_chunks(), transform, keep=keep, label="Sioux Falls OpenData")
    dropped = ", ".join(f"{n:,} {site}" for site, n in COUNTS.not_a_tree.most_common())
    print(
        f"Sioux Falls ingest: dropped {sum(COUNTS.not_a_tree.values()):,} rows that are "
        f"not standing trees ({dropped}), {COUNTS.no_id} with no GlobalID, "
        f"{COUNTS.no_geometry} with no geometry; nulled {COUNTS.placeholder_year:,} "
        f"inventory-year ({INVENTORY_YEAR}) install years and "
        f"{COUNTS.out_of_range_year} out of range",
        file=sys.stderr,
    )
    print(
        f"Sioux Falls ingest: {COUNTS.from_common:,} trees with no Latin name read "
        f"from their common name; {sum(COUNTS.unresolved_common.values()):,} common "
        f"names unresolved, top: {COUNTS.unresolved_common.most_common(40)}",
        file=sys.stderr,
    )
    unparsed = sum(COUNTS.unparsed.values())
    if unparsed:
        print(
            f"Sioux Falls ingest: {unparsed} species values unreadable: "
            f"{COUNTS.unparsed.most_common(10)}",
            file=sys.stderr,
        )
    if COUNTS.named and unparsed / COUNTS.named > MAX_UNPARSED_SHARE:
        raise RuntimeError(
            f"Sioux Falls ingest: {unparsed} of {COUNTS.named} species values "
            "unreadable; the Species field's format has changed"
        )
    table = validate_coordinates(table, city="Sioux Falls", city_code="USFSD")
    table = enforce_tree_schema(
        table, city="Sioux Falls", data_source="SIOUXFALLS_OPENDATA"
    )
    emit(table)

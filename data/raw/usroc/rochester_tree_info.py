#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Rochester, NY's public tree inventory, from the City's Forestry Division.

Licence: the DataROC Terms of Use (https://data.cityofrochester.gov/pages/terms-of-use),
which the item's `licenseInfo` points to ("This data was made available by
the City of Rochester, NY for public use").  They are terms, not a named open
licence: they permit use and require "Any user of this website providing any
software application or derivative application using data supplied at this
website" to show the City's disclaimer where the application is accessed
("The data made available here has been modified for use from its original
source, which is the official website of the City of Rochester, NY. ..."),
and they let the City require a user to stop display or distribution.

Source: "Trees Open Data - Live" (ArcGIS item 4c209944e2984b4a908a14b0cbe48075,
owner `Open_Data_Admin`, access information "Forestry Division, Department of
Environmental Services"), served from the City's own ArcGIS Server as
`Open_Data/Trees_Open_Data/FeatureServer/0`.  64,885 rows on 2026-11-20 --
street trees (`THEME_VAL` = S), park and cemetery trees (P) and a few on
vacant lots (V) -- stored in New York State Plane West feet (wkid 2262) and
reprojected to WGS84 by the server.  The Forestry "Street Tree Viewer" serves
the same rows; its planting sites and stumps are separate layers that are not
read.  The layer publishes no `editingInfo` and no GlobalID.

**The id is `ASSETID`** (`roc-{ASSETID}`), the City's asset number: non-null
on every row and unique on all but 275 values.  `OBJECTID` is not dense
(64,885 rows up to 138,372: an edited layer, not a reload), but it is a row
number.  A repeated `ASSETID` is resolved here: rows of one group that are
the same tree (within `SAME_TREE_METRES`, same species code and diameter;
232 of the 275 groups on 2026-11-20 are exact copies) keep one row, the
lowest `OBJECTID`, as `roc-{ASSETID}`; a group that holds different trees
keeps each as `roc-{ASSETID}-{OBJECTID}` (Baltimore's rule).

**Species is `GENUS` + `SPECIES`** (upper-case Latin, `SPECIES` = `SPECIES`
/ `HYBRID` / `0` for a genus-level record), the binomial the Forestry species
code (`TREE_NAME_VAL`, an upper-case common name with an abbreviated
cultivar, "MAPLE - NRWY 'CRIMSON KING'") stands for.  `GENUS_FIXES` and
`EPITHET_FIXES` read the misspellings and hybrid marks the code table
carries (`GINGKO`, `GYMOCLADUS`, `STYPHOLOBIUM JAPONICA`, `XCARNEA`,
`ALBA X ROBUR`).  `MALUS ANGUSTIFOLIA` is the code table's binomial for the
generic "CRABAPPLE" code (481 street trees; southern crabapple is not a
Rochester street tree) and is published as `Malus`.  About 700 rows carry
a newer species code with no `GENUS`/`SPECIES` filled in ("CHERRY - RANCHO
SARG", "CRABAPPLE - 'RUBY DAYZE'"); `CODE_ONLY` reads those codes, and a
code it does not know is published as Unknown, refused above
`MAX_UNREAD_CODE_SHARE` of the layer.

`cultivar` comes from `CULTIVARS`, keyed by the species code with its
abbreviations spelled out ('IVSIL' is Ivory Silk, 'BDG' Bloodgood); a code
that names two cultivars ('CHANT OR CLEVE') publishes none.

**Not every row is a tree.**  The species code records sites too: "NOT A
PLANTING SITE" (497), "NO TREE SITE", "PLANTING SITE SMALL/MEDIUM/LARGE" and
"STUMP"; `GENUS` repeats it (`0`, `NO`, `PLANTING SITE ...`, `STUMP`).  A row
either column calls a site is dropped.  "OTHER SPECIES", "UNKNOWN 1" and
"PRIVATE TREE" are trees of unrecorded species and stay as Unknown, as do
rows with neither a code nor a genus.  `MAINT_VAL` = DEAD / REMOVE are
standing trees awaiting work and stay.

**DBH is `DBH_VAL`, a string of whole inches** (`18"`; median 16, 95th
percentile 36), parsed here and refused if more than 1% will not read.  The
layer records no planting date (`INV_DATE` is the inventory visit), no
height and no crown spread.
"""

import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    UNKNOWN_SPECIES,
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://maps.cityofrochester.gov/server/rest/services/Open_Data/"
    "Trees_Open_Data/FeatureServer/0",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,ASSETID,GENUS,SPECIES,TREE_NAME_VAL,DBH_VAL"
# maxRecordCount is 120,000; a 30 MB single page is needless.
PAGE_SIZE = 5000

# Rows of one repeated ASSETID closer than this, with the same species code
# and diameter, are one tree recorded twice.
SAME_TREE_METRES = 2.0

MAX_UNREAD_CODE_SHARE = 0.01
MAX_UNREAD_DBH_SHARE = 0.01

# --- Species ----------------------------------------------------------------

# `GENUS` values the code table misspells.
GENUS_FIXES = {
    "GINGKO": "Ginkgo",
    "GYMOCLADUS": "Gymnocladus",
    "ZELCOVA": "Zelkova",
    "AMERANCHIER": "Amelanchier",
    "KOLREUTERIA": "Koelreuteria",
    "CRATEAGUS": "Crataegus",
    "POPLAR": "Populus",
    "FABACEA": "Wisteria",  # the family, on the "WISTERIA" code
    "STYPHOLOBIUM": "Styphnolobium",
    "BUDDLEIA": "Buddleja",
}

# `SPECIES` values that mean the record names only the genus.
GENUS_ONLY_EPITHETS = frozenset({"", "0", "SPECIES", "HYBRID", "X HYBRID", "X"})

# (genus, SPECIES) -> the epithet as published; None publishes the genus.
EPITHET_FIXES: dict[tuple[str, str], str | None] = {
    ("Acer", "SACCHRUM"): "saccharum",
    ("Acer", "RUBRA"): "rubrum",
    ("Acer", "X TRUNCATUM"): "truncatum",
    ("Acer", "BUERGERANUM"): "buergerianum",
    ("Aesculus", "XCARNEA"): "x carnea",
    ("Betula", "ALAGHANIENSIS"): "alleghaniensis",
    ("Betula", "HERITAGE"): "nigra",  # 'Heritage' river birch
    ("Buddleja", "DAVIDIA"): "davidii",
    ("Crataegus", "CRUSGALLI"): "crus-galli",
    ("Magnolia", "LILIIFLORIA"): "liliiflora",
    ("Magnolia", "SOULANGIANA"): "x soulangeana",
    ("Magnolia", "X SOULANGEANA"): "x soulangeana",
    ("Malus", "ANGUSTIFOLIA"): None,  # the generic CRABAPPLE code
    ("Picea", "ORIENTALS"): "orientalis",
    ("Platanus", "ACERIFOLIA"): "x acerifolia",
    ("Prunus", "'FRANKTHRESS'"): "cerasifera",  # 'Frankthrees' Mt. St. Helens plum
    ("Pyrus", "CALLERIANA"): "calleryana",
    ("Quercus", "ALBA X ROBUR"): "x bimundorum",
    ("Quercus", "ROBUR X BICOLOR"): "x warei",
    ("Quercus", "BEBBIANA"): "x bebbiana",
    ("Quercus", "JACKIANA"): "x jackiana",
    ("Quercus", "SARGENTII"): "x sargentii",
    ("Quercus", "SAULII"): "x saulii",
    ("Quercus", "SCHUETTEI"): "x schuettei",
    ("Styphnolobium", "JAPONICA"): "japonicum",
    ("Syringa", "HYACINTHIFLORA"): "x hyacinthiflora",
    ("Tilia", "EUCHLORA"): "x euchlora",
    ("Ulmus", "PARVIFOIRA"): "parvifolia",
}

# `GENUS` values that are a whole binomial.
GENUS_BINOMIALS = {"QUERCUS IMBRICARIA": "Quercus imbricaria"}

# Species codes (normalised by `code_key`) that record a site, not a tree.
SITE_CODES = frozenset(
    {
        "NOT A PLANTING SITE",
        "NO TREE SITE",
        "PLANTING SITE SMALL",
        "PLANTING SITE MEDIUM",
        "PLANTING SITE LARGE",
        "STUMP",
    }
)
SITE_GENERA = ("0 0", "NO TREE", "PLANTING SITE", "STUMP")

# Codes for a tree whose species was not recorded.
UNKNOWN_CODES = frozenset({"OTHER SPECIES", "UNKNOWN 1", "PRIVATE TREE"})

# Species codes published with no GENUS/SPECIES: code -> (species, cultivar).
CODE_ONLY: dict[str, tuple[str, str | None]] = {
    "CHERRY RANCHO SARG": ("Prunus sargentii", "Rancho"),
    "CRABAPPLE RUBY DAYZE": ("Malus", "Ruby Dayze"),
    "MAPLE SUGAR FALL FIESTA": ("Acer saccharum", "Fall Fiesta"),
    "LINDEN AMERICAN SENTRY": ("Tilia americana", "American Sentry"),
    "CRABAPPLE RASPBERRY SPEAR": ("Malus", "Raspberry Spear"),
    "HORNBEAM RISING FIRE": ("Carpinus caroliniana", "Rising Fire"),
    "CRABAPPLE GLADIATOR": ("Malus", "Gladiator"),
    "CRAB APPLE EMERALD SPIRE": ("Malus", "Emerald Spire"),
    "CHERRY SARGENTII PINK MIST": ("Prunus sargentii", "Pink Mist"),
    "CHERRY SARGENTII SPRING WONDER": ("Prunus sargentii", "Spring Wonder"),
    "MAPLE SUGAR POWDER KEG": ("Acer saccharum", "Powder Keg"),
    "MAGNOLIA GLAUCA": ("Magnolia virginiana", None),  # M. glauca is its synonym
    "PEAR CHASTITY": ("Pyrus calleryana", "Chastity"),
    "DOGWOOD CORNUS SAF SCENT": ("Cornus", None),
    "HAWTHORNE CRUSADER": ("Crataegus crus-galli", "Crusader"),
    "MAGNOLIA SUSAN": ("Magnolia", "Susan"),
    "CRABAPPLE REJOICE": ("Malus", "Rejoice"),
    "KATSURA RED FOX": ("Cercidiphyllum japonicum", "Red Fox"),
    "MAPLE FIREFALL": ("Acer x freemanii", "Firefall"),
    "APPLE HONEYCRISP": ("Malus domestica", "Honeycrisp"),
    "APPLE RED DELICIOUS": ("Malus domestica", "Red Delicious"),
    "APPLE ZESTAR": ("Malus domestica", "Zestar"),
    "APPLE CORTLAND": ("Malus domestica", "Cortland"),
    "APPLE LIBERTY": ("Malus domestica", "Liberty"),
    "APPLE HARALRED": ("Malus domestica", "Haralred"),
    "CRABAPPLE PINKSPIRE": ("Malus", "Pinkspire"),
    "CRABAPPLE RED BARRON": ("Malus", "Red Baron"),
    "CHERRY SNOW GOOSE": ("Prunus", "Snow Goose"),
    "CHERRY THUNDER CLOUD": ("Prunus cerasifera", "Thundercloud"),
    "CHERRY SWEET BLACK GOLD": ("Prunus avium", "Black Gold"),
    "CHERRY SWEET STELLA": ("Prunus avium", "Stella"),
    "PEACH ORNAMENTAL": ("Prunus persica", None),
    "EMPRESS TREE": ("Paulownia tomentosa", None),
    "MAPLE STRIPED": ("Acer pensylvanicum", None),
    "MAPLE RED SUN VALLEY": ("Acer rubrum", "Sun Valley"),
    "ZELKOVA GREEN VASE": ("Zelkova serrata", "Green Vase"),
}

# Species code -> cultivar, abbreviations spelled out.  A code naming two
# cultivars ("PEAR - 'CHANT OR CLEVE'") or a growth form is left out.
CULTIVARS: dict[str, str] = {
    "MAPLE NRWY CRIMSON KING": "Crimson King",
    "MAPLE NRWY SCHWEDLER": "Schwedleri",
    "MAPLE NRWY COLUMN": "Columnare",
    "MAPLE NRWY NORWEG SUNSET": "Norwegian Sunset",
    "MAPLE NRWY GLOBOSUM": "Globosum",
    "MAPLE GLOBOSUM": "Globosum",
    "MAPLE OLMSTED": "Olmsted",
    "JAP TREE LILAC IVSIL": "Ivory Silk",
    "JAPANESE TREE LILAC SNOW DANCE": "Snowdance",
    "CHERRY KWANZAN": "Kanzan",
    "CHERRY AUTUMNALIS": "Autumnalis",
    "CHERRY AMAANOGAWA": "Amanogawa",
    "CHERRY SARGENT ACCOL": "Accolade",
    "CHERRY SARGENTII COLMN": "Columnaris",
    "CHERRY SARGENT PINK FLAIR": "Pink Flair",
    "CHERRY FLOWERING ROYAL BURGANDY": "Royal Burgundy",
    "CHERRY CHOKE CANANDA RED": "Canada Red",
    "CHERRY CORNELIAN TREE FORM GOLDEN GLORY": "Golden Glory",
    "MAPLE RED RED SUNSET": "Red Sunset",
    "MAPLE RED OCT GLORY": "October Glory",
    "MAPLE RED KARPICK": "Karpick",
    "MAPLE RED AUTM FLM": "Autumn Flame",
    "MAPLE RED AUTUMN RAD": "Autumn Radiance",
    "MAPLE RED FRANKSRED": "Franksred",
    "MAPLE RED NORTHWOOD": "Northwood",
    "MAPLE RED BOWHALL": "Bowhall",
    "MAPLE AUTUMN BLAZE": "Autumn Blaze",
    "MAPLE ARMSTRONG": "Armstrong",
    "MAPLE CRIMSON SUNSET": "Crimson Sunset",
    "MAPLE PACIFIC SUNSET": "Pacific Sunset",
    "MAPLE TARTARIAN HOT WINGS": "Hot Wings",
    "MAPLE AMUR FLAME": "Flame",
    "MAPLE AMUR RUBY SLIPPERS": "Ruby Slippers",
    "MAPLE SUGAR LEGACY": "Legacy",
    "MAPLE SUGAR GRN MOUN": "Green Mountain",
    "MAPLE SUGAR SUG CONE": "Sugar Cone",
    "MAPLE SUGAR SUGAR CONE": "Sugar Cone",
    "MAPLE SUGAR BONFIRE": "Bonfire",
    "MAPLE SUGAR SENECA CHEIF": "Seneca Chief",
    "MAPLE MIYABEI STATE": "State Street",
    "HORSECHESTNUT RED FT MCNR": "Fort McNair",
    "HORSECHESTNUT BRIOTTI": "Briotii",
    "HORSECHESTNUT RUBY RED": "Ruby Red",
    "ASH WHITE AUT PURPLE": "Autumn Purple",
    "ASH WHITE EMPIRE": "Empire",
    "ASH GREEN PATMORE": "Patmore",
    "ASH GREEN SUMMIT": "Summit",
    "ASH GREEN CIMMARON": "Cimmaron",
    "ASH EUROPEAN HESSEI": "Hessei",
    "HAWTHORN WINTER KING": "Winter King",
    "PEAR CLEVELAND": "Cleveland Select",
    "PEAR ARISTOCRAT": "Aristocrat",
    "PEAR RED SPIRE": "Redspire",
    "PEAR JACK": "Jack",
    "PEAR CALLERY JACK": "Jack",
    "PEAR CAPITAL": "Capital",
    "LONDON PLANETREE BDG": "Bloodgood",
    "LONDON PLANETREE EXCLAMATION": "Exclamation",
    "LONDON PLANETREE COL": "Columbia",
    "LINDEN LL GLENLEVEN": "Glenleven",
    "LINDEN LL GRENSPIRE": "Greenspire",
    "LINDEN AMRCN REDMOND": "Redmond",
    "LINDEN STERLING": "Sterling",
    "ZELKOVA GREEN VASE": "Green Vase",
    "ZELKOVA CITY SPRITE": "City Sprite",
    "ZELKOVA VILLAGE GR": "Village Green",
    "ZELKOVA MUSASHINO": "Musashino",
    "HONEYLOCUST IMPERIAL": "Imperial",
    "HONEYLOCUST SHADEMAS": "Shademaster",
    "HONEYLOCUST SKYLINE": "Skyline",
    "HONEYLOCUST STREET KEEPER": "Street Keeper",
    "HONEYLOCUST SKYCOLE": "Skycole",
    "HACKBERRY MAGNIFICA": "Magnifica",
    "CRABAPPLE PRAIRIFIRE": "Prairifire",
    "CRABAPPLE HARVEST GOLD": "Harvest Gold",
    "CRABAPPLE ROYAL RAINDROPS": "Royal Raindrops",
    "CRABAPPLE PURPLE PRINCE": "Purple Prince",
    "CRABAPPLE GOLD RAIN": "Golden Raindrops",
    "CRABAPPLE SNOW DRIFT": "Snowdrift",
    "CRABAPPLE STRWBERRY PARFAIT": "Strawberry Parfait",
    "CRABAPPLE SPRING SNOW": "Spring Snow",
    "CRABAPPLE CORALBURST": "Coralburst",
    "CRABAPPLE MADONNA": "Madonna",
    "CRABAPPLE CANDYMINT": "Candymint",
    "CRABAPPLE D WYMAN": "Donald Wyman",
    "AMUR MAACKIA MAACNIFICENT": "MaacNificent",
    "KENTUCKY COFFEETREE ESPRESSO": "Espresso",
    "GINGKO PRINCTONSENTY": "Princeton Sentry",
    "GINGKO MAGYAR": "Magyar",
    "GINGKO SAMURAI": "Samurai",
    "GINGKO SARATOGA": "Saratoga",
    "GINKO GOLDEN COLONADE": "Golden Colonnade",
    "OAK SWAMP WHITE BEACON": "Beacon",
    "OAK CRIMSON SPIRE": "Crimschmidt",
    "OAK ENGLISH SKYROCKET": "Skyrocket",
    "OAK ENGLISH SKYMASTER": "Skymaster",
    "OAK ENGLISH UPRIGHT": "Fastigiata",
    "OAK ENGLISH X SWAMP WHITE REGAL PRINCE": "Regal Prince",
    "ELM ACCOLADE AM": "Accolade",
    "ELM AMER PRINCETON": "Princeton",
    "ELM FRONTIER": "Frontier",
    "ELM DELAWARE II": "Delaware II",
    "ELM HOMESTEAD": "Homestead",
    "ELM PIONEER": "Pioneer",
    "ELM PROSPECTOR": "Prospector",
    "ELM CHINESE DYNASTY": "Dynasty",
    "ELM CHINESE BOSQUE": "Bosque",
    "HYDRANGEA LIMELIGHT": "Limelight",
    "HYDRANGEA PEEGEETREE": "Grandiflora",
    "HYDRANGEA QUICKFIRE": "Quick Fire",
    "BLACKLOCUST PUR ROBE": "Purple Robe",
    "BLACKLOCUST BESSONIANA": "Bessoniana",
    "MAGNOLIA JANE": "Jane",
    "MAGNOLIA LEONARD MESSEL": "Leonard Messel",
    "MAGNOLIA BUTTERFLIES": "Butterflies",
    "BIRCH RIVER HERITAGE": "Heritage",
    "BIRCH RIVER CLMP HRTGE": "Heritage",
    "BIRCH HERITAGE CLUMP": "Heritage",
    "BIRCH RIVER LITTLE KING SINGLE STEM": "Little King",
    "OSAGE ORANGE WHITE SHIELD SINGLE STEM": "White Shield",
    "PLUM PURPLELEAF NEWPORT": "Newport",
    "PLUM TREE FORM CRIMSON POINTE": "Crimson Pointe",
    "PLUM MT ST HELEN": "Frankthrees",
    "SERVICEBERRY ATMN BR": "Autumn Brilliance",
    "SERVICEBERRY RHILL": "Robin Hill",
    "SERVICEBERRY TRAZAM": "Trazam",
    "GOLDENRAINTREE CORAL SUN": "Coral Sun",
    "HORNBEAM EURO UPRIGT": "Fastigiata",
    "BEECH EURO PURPLEA": "Purpurea",
    "BEECH FERN LEAF": "Asplenifolia",
    "BEECH WEEPING": "Pendula",
}


def code_key(value) -> str:
    """Upper-case words of a species code, punctuation and the mis-encoded
    curly quotes (U+FFFD) dropped: "MAPLE � CRIMSON SUNSET" and "MAPLE -
    'CRIMSON SUNSET'" read alike."""
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).split())


def is_site(rec: dict) -> bool:
    genus = code_key(rec.get("GENUS"))
    name = code_key(rec.get("TREE_NAME_VAL"))
    if name in SITE_CODES:
        return True
    genus_and_epithet = code_key(f"{rec.get('GENUS') or ''} {rec.get('SPECIES') or ''}")
    return any(genus_and_epithet.startswith(g) for g in SITE_GENERA) and name not in UNKNOWN_CODES


def binomial(rec: dict) -> str | None:
    """The scientific name GENUS/SPECIES record, or None when they are blank."""
    raw_genus = " ".join(str(rec.get("GENUS") or "").upper().split())
    if not raw_genus or raw_genus == "0":
        return None
    if raw_genus in GENUS_BINOMIALS:
        return GENUS_BINOMIALS[raw_genus]
    genus = GENUS_FIXES.get(raw_genus, raw_genus.capitalize())
    epithet = " ".join(str(rec.get("SPECIES") or "").upper().split())
    if (genus, epithet) in EPITHET_FIXES:
        fixed = EPITHET_FIXES[(genus, epithet)]
        return f"{genus} {fixed}" if fixed else genus
    if epithet in GENUS_ONLY_EPITHETS:
        return genus
    return f"{genus} {epithet.lower()}"


def parse_dbh(value) -> tuple[float | None, bool]:
    """(inches, readable) for a `DBH_VAL` like `18"`."""
    text = str(value or "").strip()
    if not text:
        return None, True
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:\"|IN|'')?", text.upper())
    if not match:
        return None, False
    return float(match.group(1)), True


def metres_apart(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat = math.radians((a[1] + b[1]) / 2)
    dx = (a[0] - b[0]) * 111_320 * math.cos(lat)
    dy = (a[1] - b[1]) * 110_540
    return math.hypot(dx, dy)


def repeated_asset_ids() -> list[int]:
    """The `ASSETID` values the layer assigns to more than one row."""
    payload = get_json_with_retry(
        LAYER.query_url,
        params={
            "where": "ASSETID IS NOT NULL",
            "groupByFieldsForStatistics": "ASSETID",
            "outStatistics": json.dumps(
                [{"statisticType": "count", "onStatisticField": "OBJECTID",
                  "outStatisticFieldName": "n"}]
            ),
            "having": "COUNT(OBJECTID) > 1",
            "f": "json",
        },
        timeout=LAYER.timeout,
    )
    if "features" not in payload or payload.get("exceededTransferLimit"):
        raise RuntimeError(f"Rochester ASSETID duplicate query was incomplete: {payload!r}"[:500])
    return sorted(int(f["attributes"]["ASSETID"]) for f in payload["features"])


def resolve_repeated_ids() -> dict[int, str | None]:
    """OBJECTID -> tree_id (None: drop) for every row whose ASSETID repeats."""
    ids = repeated_asset_ids()
    groups: dict[int, list[dict]] = defaultdict(list)
    for start in range(0, len(ids), 100):
        batch = ",".join(str(i) for i in ids[start:start + 100])
        for page in iter_features(
            LAYER,
            out_fields=OUT_FIELDS,
            where=f"ASSETID IN ({batch})",
            return_geometry=True,
            out_sr=4326,
            page_size=PAGE_SIZE,
        ):
            for feature in page:
                groups[feature["attributes"]["ASSETID"]].append(feature)
    return assign_repeated(groups)


def assign_repeated(groups: dict[int, list[dict]]) -> dict[int, str | None]:
    out: dict[int, str | None] = {}
    for asset, features in groups.items():
        features = sorted(features, key=lambda f: f["attributes"]["OBJECTID"])
        first = features[0]
        a0 = first["attributes"]
        p0 = esri_point(first.get("geometry"))

        def same(f: dict) -> bool:
            a = f["attributes"]
            p = esri_point(f.get("geometry"))
            if None in p or None in p0:
                return False
            return (
                code_key(a.get("TREE_NAME_VAL")) == code_key(a0.get("TREE_NAME_VAL"))
                and code_key(a.get("GENUS")) == code_key(a0.get("GENUS"))
                and code_key(a.get("SPECIES")) == code_key(a0.get("SPECIES"))
                and str(a.get("DBH_VAL")) == str(a0.get("DBH_VAL"))
                and metres_apart((p[1], p[0]), (p0[1], p0[0])) <= SAME_TREE_METRES
            )

        if all(same(f) for f in features[1:]):
            out[a0["OBJECTID"]] = f"roc-{asset}"
            for f in features[1:]:
                out[f["attributes"]["OBJECTID"]] = None
        else:
            for f in features:
                out[f["attributes"]["OBJECTID"]] = f"roc-{asset}-{f['attributes']['OBJECTID']}"
    return out


class Rochester:
    def __init__(self, repeated: dict[int, str | None]) -> None:
        self.repeated = repeated
        self.dropped: Counter[str] = Counter()
        self.unread_codes: Counter[str] = Counter()
        self.unread_dbh: Counter[str] = Counter()
        self.seen = 0

    def keep(self, feature: dict) -> bool:
        self.seen += 1
        rec = feature.get("attributes") or {}
        if rec.get("ASSETID") is None:
            self.dropped["no ASSETID"] += 1
            return False
        if is_site(rec):
            self.dropped["site or stump (species code or GENUS)"] += 1
            return False
        if self.repeated.get(rec["OBJECTID"], "") is None:
            self.dropped["second copy of a repeated ASSETID"] += 1
            return False
        return True

    def species_and_cultivar(self, rec: dict) -> tuple[str, str | None]:
        code = code_key(rec.get("TREE_NAME_VAL"))
        sp = binomial(rec)
        if sp is not None:
            return sp, CULTIVARS.get(code)
        if code in CODE_ONLY:
            return CODE_ONLY[code]
        if code and code not in UNKNOWN_CODES:
            self.unread_codes[str(rec.get("TREE_NAME_VAL"))] += 1
        return UNKNOWN_SPECIES, None

    def __call__(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str] = []
        cultivar: list[str | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            tree_id.append(self.repeated.get(rec["OBJECTID"]) or f"roc-{rec['ASSETID']}")
            sp, cv = self.species_and_cultivar(rec)
            species.append(sp)
            cultivar.append(cv)
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            inches, readable = parse_dbh(rec.get("DBH_VAL"))
            if not readable:
                self.unread_dbh[str(rec.get("DBH_VAL"))] += 1
            dbh.append(inches)

        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["USROC"] * n, type=pa.string()),
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
            print(f"Rochester: dropped {n:,} rows: {reason}", file=sys.stderr)
        for code, n in self.unread_codes.most_common():
            print(f"Rochester: {n:,} rows with unread species code {code!r} published as Unknown", file=sys.stderr)
        if not self.seen:
            return
        unread = sum(self.unread_codes.values())
        if unread / self.seen > MAX_UNREAD_CODE_SHARE:
            raise RuntimeError(
                f"Rochester: {unread:,} of {self.seen:,} rows carry a species code with no "
                f"GENUS and no CODE_ONLY entry ({dict(self.unread_codes.most_common(10))})"
            )
        bad = sum(self.unread_dbh.values())
        if bad / self.seen > MAX_UNREAD_DBH_SHARE:
            raise RuntimeError(
                f"Rochester: {bad:,} of {self.seen:,} DBH_VAL values do not read as inches "
                f"({dict(self.unread_dbh.most_common(10))})"
            )


def iter_row_chunks():
    """One page of features at a time, geometry in WGS84."""
    return iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326, page_size=PAGE_SIZE
    )


if __name__ == "__main__":
    rochester = Rochester(resolve_repeated_ids())
    table = stream_to_table(
        iter_row_chunks(), rochester, keep=rochester.keep, label="Rochester OpenData"
    )
    rochester.report()
    table = validate_coordinates(table, city="Rochester, NY", city_code="USROC")
    table = enforce_tree_schema(table, city="Rochester, NY", data_source="ROCHESTER_OPENDATA")
    emit(table)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy", "tzdata"]
# ///
"""Salem, Oregon's public trees, from the City of Salem's ArcGIS Online organisation.

Licence: resale prohibited by the City; displayed with attribution only, not
licensed for reuse.  The DataSalem Terms of Use
(https://data.cityofsalem.net/pages/disclaimer) say "Development of
derivative products is encouraged however copying and selling the data is
prohibited", and the item's own `licenseInfo`
(https://www.arcgis.com/home/item.html?id=e5397978d59f46ec96f72a1dfc660919)
that "The data in this product is subject to license and copyright
limitations and further distribution or resale is governed by licensing
restrictions", without stating any.  A ban on selling is not a ban on free
display (docs/REJECTED_SOURCES.md, PR #271), so the inventory is shown with
attribution and is not offered for reuse.

**Source: `CityTrees/FeatureServer/0`** (ArcGIS item
e5397978d59f46ec96f72a1dfc660919, owner `SalemGIS`), 49,250 rows on
2026-11-23: every City-maintained tree, `UNITTYPE` `Right of Way Tree`
(32,856) or `Park Tree` (16,394), every row `STATUS = Tree` and owned by
"City of Salem Parks" or "City of Salem".  It is the City's only published
tree inventory.  The layer is stored in Oregon State Plane North feet (wkid
2913); the server reprojects it to WGS84.

**The id is `UNITID`** (`slm-{UNITID}`, e.g. `slm-PKTE3909`), the asset
number of the City's maintenance system: non-null on every row and unique
over the layer (a `groupBy UNITID having count > 1` statistics query returns
nothing; `check_ids` re-asks it before every run).  `OBJECTID` (1..61,197
for 49,250 rows, gaps where trees were deleted) is the geodatabase row and is
not used; there is no GlobalID.

**Species is `TREE_GENUS_SPECIES`**, `"Quercus garryana - (Oregon white
oak)"`: the binomial before ` - (`, the common name in brackets is dropped.
Genus-only values (`Prunus spp`, `Acer species`) and `Unknown spp.` go
through the shared sanitiser.  127 rows have no species.

**`CULTIVAR` is a free-text field** the inventory crews use for cultivars
and for every other remark (`Upright`, `Flowering cherry`, `Straight spp.`,
`Possibly Acer Myabi`, `#7 pot`).  Only the values in `CULTIVARS`, cultivar
names read one by one off the 2026 layer with their misspellings folded in
(`Ft. McNair` -> `Fort McNair`, `Kwansan` -> `Kwanzan`), become `cultivar`;
the rest are counted and dropped.  Where the remark names the tree's real
species and the species column only has the genus or `Unknown`
(`Quercus spp.` with `Quercus buckleyi (Texas red oak)`), `NOTE_SPECIES`
takes the species from the remark; and a red maple whose remark says it is
a Freeman maple (`Freemanii`, `Upright (x freemanii)`, and the misspelt
`Freemontii`) is published as `Acer x freemanii`.

**DBH is `DBH`, inches** (alias "DBH in Inches"; median 10.4, 95th
percentile 31.6, max 91, to 0.1 in); 208 zeros are nulled by the schema.

**`PLANTDATE`** is set on 7,438 rows, mostly since 2013 (the planting
programme's records), stored as local midnight or a local working-hours
stamp, so the date is read in Pacific time.  Five rows carry 1970-01-01/02,
the epoch placeholder, and are nulled.  Two old dates (a pear in 1852, a
giant sequoia in 1872) are the City's own heritage records and are kept.
No height or crown spread is recorded.
"""

import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (  # noqa: E402
    FeatureLayer,
    esri_ms_to_datetime,
    esri_point,
    iter_features,
)
from shared.ingest import (  # noqa: E402
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services.arcgis.com/kIA6yS9KDGqZL7U3/arcgis/rest/services/"
    "CityTrees/FeatureServer/0",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,UNITID,TREE_GENUS_SPECIES,CULTIVAR,DBH,PLANTDATE,STATUS"

PACIFIC = ZoneInfo("America/Los_Angeles")
# 1970-01-01 local (or 1970-01-02 08:00 UTC, the same epoch written from a
# date-only field): no City tree was planted on the epoch.
PLACEHOLDER_PLANT_YEAR = 1970

# Every row of the 2026 layer is a tree; another STATUS is a meaning nobody
# has read, so it raises rather than guessing.
KEEP_STATUS = "Tree"

GENUS_ONLY = frozenset({"spp", "spp.", "sp", "sp.", "species"})

# `TREE_GENUS_SPECIES` values the binomial-before-the-bracket rule reads
# wrong: the raw binomial (before ` - (`) -> (species, cultivar).
NAME_FIXES: dict[str, tuple[str, str | None]] = {
    # 'Hybridus Pendulus' is a cultivar of the willowleaf cotoneaster
    # hybrids, not an epithet; the common name says "Cotoneaster pendulus".
    "cotoneaster hybridus pendulus": ("Cotoneaster", "Hybridus Pendulus"),
}

# Remarks in `CULTIVAR` that name the tree's species, applied only where the
# species column has just the genus (or Unknown, or nothing) and the genus
# agrees: normalised remark -> (species, cultivar).
NOTE_SPECIES: dict[str, tuple[str, str | None]] = {
    "quercus buckleyi (texas red oak)": ("Quercus buckleyi", None),
    "q buckleyi (texas red oak)": ("Quercus buckleyi", None),
    "quercus bucklii - (texas red oak)": ("Quercus buckleyi", None),
    "quercus imbricaria - (shingle oak)": ("Quercus imbricaria", None),
    "quercus imbricaria (shingle oak)": ("Quercus imbricaria", None),
    "quercus prinus - (chestnut oak)": ("Quercus montana", None),
    "quercus montana (chestnut oak)": ("Quercus montana", None),
    "quercus montana": ("Quercus montana", None),
    "quercus engelmannii - (mesa oak)": ("Quercus engelmannii", None),
    "q grisea": ("Quercus grisea", None),
    "shumardii": ("Quercus shumardii", None),
    "q bimundorum (prairie stature oak)": ("Quercus x bimundorum", "Prairie Stature"),
    "alnus hirsuta (manchurian alder) 'prairie horizon'": ("Alnus hirsuta", "Prairie Horizon"),
    "sitka spruce (picea sitchensis)": ("Picea sitchensis", None),
    "angustifolia": ("Fraxinus angustifolia", None),
    "frax. angustifolia": ("Fraxinus angustifolia", None),
    "frax. angustifolia (narrowed-leaved ash)": ("Fraxinus angustifolia", None),
    "fraxinus mandshurica": ("Fraxinus mandshurica", None),
    "hamamelis virginia (american witch hazel)": ("Hamamelis virginiana", None),
    "italian cypress (cupressus sempervirens)": ("Cupressus sempervirens", None),
    "cupressus arizonica (arizona cypress)": ("Cupressus arizonica", None),
    "halesia carolina (silverbell)": ("Halesia carolina", None),
    "halesia tetraptera - (carolina silverbell)": ("Halesia tetraptera", None),
    "prunus dulcis (almond)": ("Prunus dulcis", None),
    "prunus arminiaca (apricot)": ("Prunus armeniaca", None),
    "prunus campanulata (taiwan cherry)": ("Prunus campanulata", None),
    "big cis (prunus x cistena)": ("Prunus x cistena", "Big Cis"),
    "eucalyptus cinerea (silver dollar gum)": ("Eucalyptus cinerea", None),
    "empress tree (paulownia tomentosa)": ("Paulownia tomentosa", None),
    "dove tree (davidia involucrata)": ("Davidia involucrata", None),
    "cedrus libani 'glauca pendula'": ("Cedrus libani", "Glauca Pendula"),
    "cedrus libani - 'glauca pendula'": ("Cedrus libani", "Glauca Pendula"),
    "acer grandidentatum - (bigtooth maple) 'rocky mountain glow'": (
        "Acer grandidentatum", "Rocky Mountain Glow"),
}

# A red maple the crew recorded as a Freeman maple (A. rubrum x saccharinum).
FREEMANII_NOTE = re.compile(r"\bfre+m[ao]n|\bfremontii|\bfreemontii", re.IGNORECASE)

# Cultivar names in `CULTIVAR`, as published -> canonical.  Keys are the
# normalised remark (`note_key`); a canonical name is also its own key.
_CANONICAL = (
    "Akebono", "Allee", "American Dream", "Arapaho", "Armstrong", "Arnold's Pride",
    "Aurea", "Aurea Wells", "Autumn Brilliance", "Autumn Flame", "Autumn Gold",
    "Autumn Purple", "Bartlett", "Bloodgood", "Bosque", "Bowhall", "Brackens Brown Beauty",
    "Bradford", "Briotii", "Bush's Electra", "Camperdownii", "Canada Red",
    "Cardinal Royal", "Chanticleer", "Cimmaron", "City Sprite", "Cleveland Select",
    "Columnaris", "Corinthian", "Crimson King", "Crimson Sentry", "Crimson Spire",
    "Dawyck", "Dawyck Purple", "Deborah", "Dissectum", "Drummondii", "Dura Heat",
    "Edith Bogue", "Emerald Avenue", "Emerald Beauty", "Emerald City", "Emerald Lustre",
    "Emerald Queen", "Emerald Sunshine", "Espresso", "Exclamation", "Fairview",
    "Fairview Flame", "Fastigiata", "Firestarter", "Flame", "Forest Green",
    "Forest Pansy", "Fort McNair", "Frans Fontaine", "Frontier", "Galaxy", "Gishiki",
    "Glauca", "Golden Colonnade", "Golden Desert", "Goldcrest", "Green Gable",
    "Green Vase", "Green Whisper", "Greenspire", "Greenstreet", "Halka", "Heartland",
    "Homestead", "Imperial", "Italica", "Ivory Silk", "Jade Prince",
    "Jefferson", "Jeffersred", "Karl Fuchs", "Karpick", "Krauter Vesuvius", "Kwanzan",
    "Lincoln", "Long", "MaacNificent", "Magyar", "Majestic", "Merlot", "Midnight Express",
    "Moonglow", "Morton", "Morton Circle", "Mount Fuji", "Musashino", "Muskogee",
    "Natchez", "Native Flame", "New Harmony", "Newport", "Nishiki Gawa",
    "Norwegian Sunset", "October Glory", "Okame", "Olmsted", "Pacific Fire", "Panacek",
    "Patriot", "Pendula", "Pink Chimes", "Pink Dawn", "Prairie Stature", "Prairifire",
    "Presidential Gold", "Princeton", "Princeton Sentry", "Queen Elizabeth",
    "Red Push", "Red Rage", "Red Sunset", "Redmond", "Riversii", "Rocky Mountain Glow",
    "Rotfuchs", "Rotundiloba", "Royal Gem", "Royal Red", "Royal Star",
    "Rubra", "Ruby Vase", "Rustica Rubra", "Sango Kaku", "Scanlon",
    "Scarlet Sentinel", "Schmidt", "Schwedleri", "Shademaster", "Shawnee Brave",
    "Shidare Yoshino", "Shirofugen", "Skinny Genes", "Skyline", "Skyrocket",
    "Snowcone", "Spring Flurry", "Starburst", "Starlight", "Sterling", "Summer Storm",
    "Summit", "Sunburst", "Tamukeyama", "The President", "Thundercloud", "Triumph",
    "True North", "Tupelo Tower", "Tuscarora", "Tuskegee", "Urban Pinnacle",
    "Valley Forge", "Vanderwolf's Pyramid", "Vanessa", "Venus", "Victoria",
    "Village Green", "Virescens", "Vulcan", "Warrenred", "Western Son", "White Shield",
    "Wichita", "Wildfire", "Wireless", "Wisconsin Red",
)
_ALIASES: dict[str, str] = {
    "accolade": "Morton",  # 'Accolade' is the trade name of Ulmus 'Morton'
    "accolades": "Morton",
    "autom purple": "Autumn Purple",
    "automapurple": "Autumn Purple",
    "autumn purple ash": "Autumn Purple",
    "blood good": "Bloodgood",
    "briotti": "Briotii",
    "briottii": "Briotii",
    "bush?s electra": "Bush's Electra",
    "camper down": "Camperdownii",
    "camperdown": "Camperdownii",
    "cardianl royal": "Cardinal Royal",
    "chantaclere": "Chanticleer",
    "clevelend": "Cleveland Select",
    "crimson spire' hybrid oak": "Crimson Spire",
    "dissectum (lace-leaf)": "Dissectum",
    "emerald ave": "Emerald Avenue",
    "emperial": "Imperial",
    "exclamation!": "Exclamation",
    "forest pansie": "Forest Pansy",
    "franz fontaine": "Frans Fontaine",
    "frontier elm": "Frontier",
    "ft mcnair": "Fort McNair",
    "ft. mcnair": "Fort McNair",
    "galaxy magnolia": "Galaxy",
    "glaucus pendula": "Glauca Pendula",
    "green gab": "Green Gable",
    "green spire": "Greenspire",
    "green village": "Village Green",
    "karpic": "Karpick",
    "krauters vesuvius": "Krauter Vesuvius",
    "kuanzon": "Kwanzan",
    "kwansan": "Kwanzan",
    "kwazan": "Kwanzan",
    "long (regal prince)": "Long",
    "maacnificent": "MaacNificent",
    "moon glow": "Moonglow",
    "morton circle exclamation": "Morton Circle",
    "muscogee": "Muskogee",
    "nachez": "Natchez",
    "natchez (white)": "Natchez",
    "nishiki gawa": "Nishiki Gawa",
    "okame' flowering cherry": "Okame",
    "okame cherry": "Okame",
    "panacek (metro gold)": "Panacek",
    "prairie fire": "Prairifire",
    "prairie stature oak": "Prairie Stature",
    "prairiefire": "Prairifire",
    "prairiefire crabapple": "Prairifire",
    "prairiefire' crabapple": "Prairifire",
    "redmond linden": "Redmond",
    "riversii (rivers purple beech)": "Riversii",
    "royal gem' crabapple": "Royal Gem",
    "ruby vase ironwood": "Ruby Vase",
    "sango kaku, pinebark maple": "Sango Kaku",
    "scarlet sentinal": "Scarlet Sentinel",
    "schmidt (forest green)": "Schmidt",
    "shawnee": "Shawnee Brave",
    "shdemaster": "Shademaster",
    "sky rocket": "Skyrocket",
    "tower tupelo": "Tupelo Tower",
    "triumph elm": "Triumph",
    "tuskeegee": "Tuskegee",
    "vanderwolfs pyramid": "Vanderwolf's Pyramid",
    "whiteshield": "White Shield",
    "wildfire tupelo": "Wildfire",
    "witchita": "Wichita",
}


def note_key(value: str) -> str:
    """A `CULTIVAR` remark folded for lookup: case, spacing, edge quotes and
    full stops, and a leading hybrid `x ` (`x Frontier`)."""
    text = " ".join(value.split()).lower().strip(" .'`\"")
    if text.startswith("x ") and len(text) > 2:
        text = text[2:].strip(" .'`\"")
    return text


CULTIVARS: dict[str, str] = {note_key(c): c for c in _CANONICAL}
CULTIVARS.update({"glauca pendula": "Glauca Pendula"})
CULTIVARS.update(_ALIASES)


def raw_binomial(value) -> str | None:
    """The scientific part of `TREE_GENUS_SPECIES`, before ` - (common name)`."""
    if value is None:
        return None
    text = " ".join(str(value).split())
    text = re.split(r"\s+-\s+\(", text, maxsplit=1)[0].strip()
    return text or None


def genus_only(binomial: str | None) -> bool:
    """No species recorded: nothing, `Unknown ...`, or `Genus spp`."""
    if binomial is None:
        return True
    tokens = binomial.split()
    return tokens[0].lower() == "unknown" or len(tokens) == 1 or tokens[1].lower() in GENUS_ONLY


def check_ids() -> None:
    """Refuse a layer whose `UNITID` is no longer a key."""
    payload = get_json_with_retry(
        LAYER.query_url,
        params={
            "where": "1=1",
            "groupByFieldsForStatistics": "UNITID",
            "outStatistics": json.dumps(
                [{"statisticType": "count", "onStatisticField": "OBJECTID",
                  "outStatisticFieldName": "n"}]
            ),
            "having": "COUNT(OBJECTID) > 1",
            "f": "json",
        },
        timeout=LAYER.timeout,
    )
    if "features" not in payload:
        raise RuntimeError(f"Salem UNITID duplicate query failed: {payload!r}"[:500])
    if payload["features"]:
        repeated = [f["attributes"].get("UNITID") for f in payload["features"][:10]]
        raise RuntimeError(
            f"Salem: UNITID repeats on {len(payload['features'])} values "
            f"(e.g. {repeated}); it is no longer the layer's key"
        )


class Salem:
    def __init__(self) -> None:
        self.dropped: Counter[str] = Counter()
        self.notes = Counter[str]()
        self.placeholder_dates = 0

    def keep(self, feature: dict) -> bool:
        rec = feature.get("attributes") or {}
        status = (rec.get("STATUS") or "").strip()
        if status != KEEP_STATUS:
            raise RuntimeError(
                f"Salem: unseen STATUS {status!r}; read what it means before "
                "deciding whether it is a tree"
            )
        if not (rec.get("UNITID") or "").strip():
            self.dropped["no UNITID"] += 1
            return False
        return True

    def species_and_cultivar(self, rec: dict) -> tuple[str | None, str | None]:
        binomial = raw_binomial(rec.get("TREE_GENUS_SPECIES"))
        cultivar: str | None = None
        if binomial is not None and binomial.lower() in NAME_FIXES:
            binomial, cultivar = NAME_FIXES[binomial.lower()]
        raw_note = rec.get("CULTIVAR")
        if not isinstance(raw_note, str) or not raw_note.strip():
            return binomial, cultivar
        key = note_key(raw_note)
        named = NOTE_SPECIES.get(key)
        if named is not None:
            genus = named[0].split()[0].lower()
            if genus_only(binomial) and (
                binomial is None
                or binomial.split()[0].lower() in (genus, "unknown")
            ):
                self.notes["species from remark"] += 1
                return named
            self.notes["species remark disagrees, dropped"] += 1
            return binomial, cultivar
        if binomial and binomial.lower() == "acer rubrum" and FREEMANII_NOTE.search(raw_note):
            self.notes["red maple remarked Freeman maple"] += 1
            return "Acer x freemanii", cultivar
        canonical = CULTIVARS.get(key)
        if canonical is not None:
            self.notes["cultivar"] += 1
            return binomial, canonical
        self.notes["remark not a cultivar, dropped"] += 1
        return binomial, cultivar

    def __call__(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        plant_date: list[date | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            tree_id.append(f"slm-{rec['UNITID'].strip()}")
            sp, cv = self.species_and_cultivar(rec)
            species.append(sp)
            cultivar.append(cv)
            stamp = esri_ms_to_datetime(rec.get("PLANTDATE"))
            day = stamp.astimezone(PACIFIC).date() if stamp else None
            if day is not None and day.year == PLACEHOLDER_PLANT_YEAR:
                self.placeholder_dates += 1
                day = None
            plant_date.append(day)
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            raw_dbh = rec.get("DBH")
            dbh.append(float(raw_dbh) if raw_dbh is not None else None)

        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["USSLM"] * n, type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "plant_date": pa.array(plant_date, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            }
        )

    def report(self) -> None:
        for reason, n in sorted(self.dropped.items()):
            print(f"Salem: dropped {n:,} rows: {reason}", file=sys.stderr)
        for kind, n in sorted(self.notes.items()):
            print(f"Salem: CULTIVAR remarks: {n:,} {kind}", file=sys.stderr)
        print(
            f"Salem: nulled {self.placeholder_dates} placeholder {PLACEHOLDER_PLANT_YEAR} PLANTDATEs",
            file=sys.stderr,
        )


def iter_row_chunks():
    """One ArcGIS page (2,000 features) at a time, geometry reprojected to WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


if __name__ == "__main__":
    check_ids()
    salem = Salem()
    table = stream_to_table(iter_row_chunks(), salem, keep=salem.keep, label="Salem OpenData")
    salem.report()
    table = validate_coordinates(table, city="Salem", city_code="USSLM")
    table = enforce_tree_schema(table, city="Salem", data_source="SALEM_OPENDATA")
    emit(table)

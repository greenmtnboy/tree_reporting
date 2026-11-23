#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy", "tzdata"]
# ///
"""Penticton's tree inventory, from the City of Penticton.

Source: the `Trees` layer (`External_PRD/Parks/MapServer/1355`) on the City's
own ArcGIS Server (maps.penticton.ca), "City of Penticton owned and
maintained trees": 9,665 rows on 2026-11-22, parks, trails and boulevards.
The City's Open Penticton hub catalogues the same table as "City Owned Trees"
(item e0905b0677bf4b468b57fb52d2d725db, owner CityOfPenticton), served from a
hosted copy (`services1.arcgis.com/ZMQyarkhNAnn8lip/.../Parks_PRD/
FeatureServer/1355`) that the City's `gisscripts` account refreshes from this
server; the hosted copy is not read, because of its ids (below).

Licence: the item's licence field and the hub's front page link the "Open
Government Licence - City of Penticton" (version 2, based on the Open
Government Licence - Canada 2.0,
https://www.penticton.ca/sites/default/files/docs/our-community/maps-gis/2020-07-02-PDF-Open%20Data%20Government%20Licence.pdf):
"a worldwide, royalty-free, perpetual, non-exclusive licence to use the
information, including for commercial purposes", to "copy, modify, publish,
translate, adapt, distribute or otherwise use the information", with the
attribution "Contains information licenced under the Open Government Licence
- City of Penticton".

**The id is the City server's `GlobalID`** (`pen-{guid}`), non-null and
unique over all 9,665 rows (2026-11-22).  The layer is a versioned enterprise
geodatabase edited in place: `OBJECTID` runs 1..4,028,634 with gaps, and a
removed tree stays in the table with `Status = REM`.  The hosted copy's
`GlobalID` is *not* that one: it is regenerated when the copy is reloaded
(`PTREE-1` is `{77084EA6-...}` here, `ceea7d1e-...` in the hosted PRD copy and
`a9d78398-...` in the hosted TST copy) and its `OBJECTID` is a dense 1..9,665.
`FACILITYID` (`PTREE-n`) is not an id: null on 984 rows and repeated (one
value 44 times).  A row without a GlobalID is dropped with a count.

**Not every row is a tree.**  `Status` is `ACT` or `REM` (removed: 432 rows,
dropped).  `TYPE` is Deciduous, Coniferous, `Stump` (337, dropped) or
`Planting` (241, dropped: planting sites, nearly all with no species, several
commented "planting spot" or "potential tree planting area").  An unseen value
of either refuses the build.  `InitialCondtionScore = DEAD` (67) is a dead
tree and stays.  `OWNEDBY`/`MAINTBY` are bare integers with no published
domain and are not read; the ~700 trees with `LOCATION = Private` stay, as
the City's own inventory carries them.

**Species is `GENUS` + `SPECIES`, and `SPECIES` is mostly
`Binomial (common name)`.**  The parenthesised common name is removed and the
binomial kept, curly quotes straightened so the schema lifts a quoted
cultivar (`Acer platanoides 'Crimson King'`).  `GENUS` is a picklist with the
City's own spellings (`Ginko`, `Phellodendrum`, `xChitalpa`) and the
catch-alls `Other` and `Unknown`; `Cedrus` is the City's "cedar" group and
holds no true cedar (its named rows are *Thuja occidentalis*, Port Orford
cedar and western redcedar), so it is never published as a genus.  Several
hundred rows carry a fragment of an English name in `SPECIES` instead (`White` under
Cedrus, `European` under Carpinus, `Saskatoon` under Amelanchier): those are
resolved by `FRAGMENTS` below, read against the genus they were entered
under, and a fragment the table does not know is published at its genus.
Where `SPECIES` names a different genus from `GENUS` and carries its common
name (`Thuja occidentalis (Eastern white cedar)` under Cedrus,
`Sequoiadendron giganteum` under Sequoia) the binomial wins.  A hybrid
formula (`Acer truncatum x Acer platanoides`) is published at genus.  A
parse-failure guard refuses more than 1% unreadable names.

**DBH is `DBH`, centimetres** (the field's alias is "DBH (cm)"; median 18,
p95 76, max 254), converted to inches; zero is "not measured".
**`SPREAD` is the crown spread in metres** (median 4, p95 13.7; the
feet-converted values -- 0.9144, 12.192 -- show it) and goes to
`crown_width_m`; zero is not measured.  `HEIGHT` (metres) has no column.

**`PLANTDATE`** is read as a Pacific-time date (the layer's
`dateFieldsTimeReference`; the server returns real UTC instants).  The City
stamps an estimated year as 15 May of it, which is kept, except **2019-05-15**,
the inventory's own year, stamped on 2,091 rows (1,941 of them kept trees)
whatever their size (DBH 1-254 cm, median 25, every one with `EstimatedAge`
6): that is nulled.
"""

import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_ms_to_datetime,
    esri_point,
    iter_features,
)
from shared.ingest import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://maps.penticton.ca/utility/rest/services/External_PRD/Parks/MapServer/1355",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,GlobalID,Status,TYPE,GENUS,SPECIES,DBH,SPREAD,PLANTDATE"

STATUS_KEPT = "ACT"
STATUS_DROPPED = frozenset({"REM"})
TYPE_KEPT = frozenset({"deciduous", "coniferous"})
TYPE_DROPPED = frozenset({"stump", "planting"})

PACIFIC = ZoneInfo("America/Vancouver")
# The inventory year, stamped as the planting date of trees it did not see planted.
PLACEHOLDER_PLANT_DATE = date(2019, 5, 15)

# Refuse to publish when more than this share of named rows yield no taxon.
MAX_UNPARSED_SHARE = 0.01

# The City's GENUS picklist spellings, and its catch-alls.  None: not a genus
# this ingest may publish (Cedrus is the City's "cedar" group; see above).
GENUS_FIXES: dict[str, str | None] = {
    "ginko": "Ginkgo",
    "phellodendrum": "Phellodendron",
    "xchitalpa": "Chitalpa",
    "cedrus": None,
    "other": None,
    "unknown": None,
}

# SPECIES values that are not a binomial, keyed by `genus|text` (the GENUS
# field lower-cased, `-` when empty) or by `*|text` for any genus.  The text
# is lower-cased, curly quotes straightened, the parenthesised common name
# removed.  Each answer is the publisher's own usage where the layer shows it
# (`Crataegus laevigata (English hawthorn)`, `Thuja occidentalis (Eastern
# white cedar)` under Cedrus), otherwise the one taxon the name means in a
# southern-interior BC street inventory.  None: a value that names no taxon.
FRAGMENTS: dict[str, str | None] = {
    # Cedrus: the City's cedar group.
    "cedrus|white": "Thuja occidentalis",
    "cedrus|white cedar": "Thuja occidentalis",
    "cedrus|port orford cedar": "Chamaecyparis lawsoniana",
    "cedrus|port orford": "Chamaecyparis lawsoniana",
    "cedrus|western red": "Thuja plicata",
    "cedrus|insence cedar": "Calocedrus decurrens",
    "thuja|white": "Thuja occidentalis",
    # Acer
    "acer|sugar": "Acer saccharum",
    "acer|paperbark maple": "Acer griseum",
    "acer|rubrum 'bowhall'": "Acer rubrum 'Bowhall'",
    "acer|rubrum frank jr": "Acer rubrum 'Frank Jr.'",
    "acer|acer grand 'schmidt'": "Acer grandidentatum 'Schmidt'",
    # Amelanchier
    "amelanchier|saskatoon": "Amelanchier alnifolia",
    "amelanchier|serviceberry": "Amelanchier",
    "amelanchier|juneberry": "Amelanchier",
    "amelanchier|june berry": "Amelanchier",
    "amelanchier|june": "Amelanchier",
    "amelanchier|common": "Amelanchier",
    "amelanchier|canadensis": "Amelanchier canadensis",
    "amelanchier|japanese crab apple": "Malus",
    # Betula
    "betula|river": "Betula nigra",
    "betula|river birch": "Betula nigra",
    "betula|paper": "Betula papyrifera",
    "betula|silver": "Betula pendula",
    "betula|silver birch": "Betula pendula",
    # Carpinus
    "carpinus|european": "Carpinus betulus",
    "carpinus|carolinian uxbridge": "Carpinus caroliniana 'Uxbridge'",
    "carpinus|caroliniana uxbridge": "Carpinus caroliniana 'Uxbridge'",
    # Catalpa (the City files Chitalpa under it too)
    "catalpa|strawberry moon": "Chitalpa tashkentensis 'Strawberry Moon'",
    "catalpa|tashkentesis": "Chitalpa tashkentensis",
    "catalpa|southern": "Catalpa bignonioides",
    "catalpa|southern catalpa": "Catalpa bignonioides",
    "catalpa|northern": "Catalpa speciosa",
    # Cercidiphyllum, Cercis, Cladrastis, Cornus
    "cercidiphyllum|japanese": "Cercidiphyllum japonicum",
    "cercidiphyllum|japonicum": "Cercidiphyllum japonicum",
    "cercis|eastern": "Cercis canadensis",
    "cercis|eastern redbud": "Cercis canadensis",
    "cercis|midnight express eastern redbud": "Cercis canadensis 'Midnight Express'",
    "cladrastis|american": "Cladrastis kentukea",
    "cladrastis|american yellowwood": "Cladrastis kentukea",
    "cladrastis|american yellow wood": "Cladrastis kentukea",
    "cladrastis|yellowood": "Cladrastis kentukea",
    "cladrastis|perkins pink": "Cladrastis kentukea 'Perkins Pink'",
    "cladrastis|yellowwood 'perkins pink'": "Cladrastis kentukea 'Perkins Pink'",
    "cornus|flowering": "Cornus florida",
    "cornus|flowering dogwood": "Cornus florida",
    "cornus|kousa": "Cornus kousa",
    # Corylus, Crataegus
    "corylus|beaked hazelnut": "Corylus cornuta",
    "crataegus|black": "Crataegus douglasii",
    "crataegus|black hawthorn": "Crataegus douglasii",
    "crataegus|english": "Crataegus laevigata",
    "crataegus|midland hawthorn": "Crataegus laevigata",
    "crataegus|cratagegus x murdersis 'snowbird' snowbird hawthorn": "Crataegus x mordenensis 'Snowbird'",
    # Fraxinus, Gleditsia, Gymnocladus, Juglans
    "fraxinus|narrow leafed": "Fraxinus angustifolia",
    "gleditsia|honey locust": "Gleditsia triacanthos",
    "gymnocladus|kentucky coffee tree 'espresso'": "Gymnocladus dioicus 'Espresso'",
    "juglans|english": "Juglans regia",
    "juglans|english walnut": "Juglans regia",
    # Magnolia, Malus, Morus
    "magnolia|kobus": "Magnolia kobus",
    "magnolia|little gem": "Magnolia grandiflora 'Little Gem'",
    "magnolia|sweetbay": "Magnolia virginiana",
    "magnolia|star": "Magnolia stellata",
    "malus|siberian crabapple": "Malus baccata",
    "morus|black": "Morus nigra",
    "morus|red mulberry": "Morus rubra",
    # Picea, Pinus, Populus, Prunus, Pyrus
    "picea|sitka": "Picea sitchensis",
    "picea|white": "Picea glauca",
    "pinus|ponderosa": "Pinus ponderosa",
    "pinus|eastern white pine": "Pinus strobus",
    "populus|silver": "Populus alba",
    "prunus|sour cherry": "Prunus cerasus",
    "prunus|vic shubert": "Prunus virginiana 'Schubert'",
    "pyrus|communis": "Pyrus communis",
    # Quercus
    "quercus|black": "Quercus velutina",
    "quercus|black oak": "Quercus velutina",
    "quercus|chestnut leaved oak": "Quercus castaneifolia",
    "quercus|chestnut-leaved oak": "Quercus castaneifolia",
    "quercus|pedunculate oak": "Quercus robur",
    # Taxus, Tilia
    "taxus|japanese": "Taxus cuspidata",
    "tilia|big leaf": "Tilia platyphyllos",
    # Any genus (Other, Unknown, empty, or a common name of its own).
    "*|tree of heaven": "Ailanthus altissima",
    "*|russian olive": "Elaeagnus angustifolia",
    "*|rhus typhina": "Rhus typhina",
    "*|staghorn sumac": "Rhus typhina",
    "*|celtis occidentalis": "Celtis occidentalis",
    "*|ultra hackberry": "Celtis occidentalis",
    "*|beautybush": "Kolkwitzia amabilis",
    "*|oriental arbor vitae": "Platycladus orientalis",
    "*|platycladus orientalist": "Platycladus orientalis",
    "*|swedish whitebeam": "Sorbus intermedia",
    "*|rose of sherion": "Hibiscus syriacus",
    "*|peking cotoneaster": "Cotoneaster acutifolius",
    "*|japanese pagoda tree": "Styphnolobium japonicum",
    "*|virginia creeper": "Parthenocissus quinquefolia",
    "*|crepe myrtle 'colorama scarlet'": "Lagerstroemia indica 'Colorama Scarlet'",
    "*|scarlet crape myrtle": "Lagerstroemia indica",
    "*|dravidian involucrata": "Davidia involucrata",
    "*|hinoki false cypress": "Chamaecyparis obtusa",
    "*|siberian pea tree": "Caragana arborescens",
    "*|large leaf spindle": "Euonymus latifolius",
    "*|viburnum macrocephalum": "Viburnum macrocephalum",
    "*|viburnum trilbum": "Viburnum trilobum",
    "*|european privet": "Ligustrum vulgare",
    "*|forsythia viridissima": "Forsythia viridissima",
    "*|dwarf honeysuckle": "Lonicera",
    "*|styrax 'snowcone'": "Styrax japonicus 'Snowcone'",
    "*|vine": None,
    "*|european": None,
}

# Values that say "not recorded".
EMPTY_SPECIES = frozenset({"", "unknown", "<null>", "other", "null"})

QUOTES = str.maketrans({"‘": "'", "’": "'", "“": "'", "”": "'", "`": "'"})
COMMON_NAME = re.compile(r"\s*\(.*$")
HYBRID_MARKS = frozenset({"x", "×"})


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).translate(QUOTES).split())
    return text or None


def genus_of(value) -> tuple[str | None, bool]:
    """(publishable genus, whether the field named one at all)."""
    text = clean(value)
    if text is None:
        return None, False
    key = text.lower()
    if key in GENUS_FIXES:
        return GENUS_FIXES[key], key not in ("other", "unknown")
    return (text[:1].upper() + text[1:].lower(), True) if text.isalpha() else (None, False)


def binomial(text: str) -> str | None:
    """`Genus epithet ['Cultivar']` with the case normalised, or None if it is not one.

    A hybrid formula (`Acer truncatum x Acer platanoides`) is the genus alone.
    """
    head, quote, tail = text.partition("'")
    tokens = head.split()
    # `Genus 'Cultivar'` is a name too (`Magnolia 'Galaxy'`).
    if len(tokens) < (1 if quote else 2) or not tokens[0].isalpha():
        return None
    genus = tokens[0][:1].upper() + tokens[0][1:].lower()
    rest = [t.lower() for t in tokens[1:]]
    marks = [i for i, t in enumerate(rest) if t in HYBRID_MARKS]
    if any(i > 0 for i in marks):
        return genus
    for token in rest:
        if not re.fullmatch(r"[a-z×][a-z.-]*", token):
            return None
    name = " ".join([genus, *rest])
    if quote:
        cultivar = tail.strip().strip("'").strip()
        if cultivar:
            name = f"{name} '{cultivar[:1].upper()}{cultivar[1:]}'"
    return name


class Penticton:
    def __init__(self) -> None:
        self.dropped: Counter[str] = Counter()
        self.unparsed: Counter[str] = Counter()
        self.at_genus: Counter[str] = Counter()
        self.named = 0
        self.placeholder_dates = 0

    def keep(self, feature: dict) -> bool:
        rec = feature.get("attributes") or {}
        status = clean(rec.get("Status")) or ""
        if status in STATUS_DROPPED:
            self.dropped[f"Status {status}"] += 1
            return False
        if status != STATUS_KEPT:
            raise RuntimeError(f"Penticton: unseen Status value {rec.get('Status')!r}")
        kind = (clean(rec.get("TYPE")) or "").lower()
        if kind in TYPE_DROPPED:
            self.dropped[f"TYPE {kind}"] += 1
            return False
        if kind not in TYPE_KEPT:
            raise RuntimeError(f"Penticton: unseen TYPE value {rec.get('TYPE')!r}")
        if clean(rec.get("GlobalID")) is None:
            self.dropped["no GlobalID"] += 1
            return False
        return True

    def species_of(self, genus_raw, species_raw) -> str | None:
        genus, named_genus = genus_of(genus_raw)
        raw = clean(species_raw) or ""
        had_common_name = "(" in raw
        text = COMMON_NAME.sub("", raw).strip()
        key = text.lower()
        genus_key = (clean(genus_raw) or "-").lower()
        if key in EMPTY_SPECIES:
            if genus is None and named_genus:
                self.unparsed[f"{genus_raw}|{raw}"] += 1
            if genus is not None or named_genus:
                self.named += 1
            return genus
        self.named += 1
        for lookup in (f"{genus_key}|{key}", f"*|{key}"):
            if lookup in FRAGMENTS:
                return FRAGMENTS[lookup]
        name = binomial(text)
        if name is not None:
            first = name.split()[0]
            # A binomial under another genus (or none) is trusted only in the
            # layer's own `Binomial (common name)` form.
            if first == genus or had_common_name:
                return name
        if genus is not None:
            self.at_genus[f"{genus_raw}|{raw}"] += 1
            return genus
        self.unparsed[f"{genus_raw}|{raw}"] += 1
        return None

    def transform(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        plant_date: list[date | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []
        crown: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            guid = clean(rec["GlobalID"]).strip("{}").lower()
            tree_id.append(f"pen-{guid}")
            species.append(self.species_of(rec.get("GENUS"), rec.get("SPECIES")))
            stamp = esri_ms_to_datetime(rec.get("PLANTDATE"))
            day = stamp.astimezone(PACIFIC).date() if stamp else None
            if day == PLACEHOLDER_PLANT_DATE:
                self.placeholder_dates += 1
                day = None
            plant_date.append(day)
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            cm = rec.get("DBH")
            dbh.append(cm_to_inches(cm) if cm is not None and cm > 0 else None)
            spread = rec.get("SPREAD")
            crown.append(float(spread) if spread is not None and spread > 0 else None)

        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["CAPEN"] * n, type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "plant_date": pa.array(plant_date, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
                "crown_width_m": pa.array(crown, type=pa.float64()),
            }
        )

    def report(self) -> None:
        print(
            "Penticton: dropped "
            + (", ".join(f"{v} {k!r}" for k, v in sorted(self.dropped.items())) or "nothing")
            + f"; nulled {self.placeholder_dates} placeholder PLANTDATE {PLACEHOLDER_PLANT_DATE}"
            + f"; {sum(self.at_genus.values())} unrecognised SPECIES value(s) published at genus",
            file=sys.stderr,
        )
        if self.at_genus:
            print(
                "Penticton: at genus: "
                + ", ".join(f"{k!r} x{v}" for k, v in self.at_genus.most_common()),
                file=sys.stderr,
            )
        failed = sum(self.unparsed.values())
        if failed:
            print(
                f"Penticton: {failed} row(s) with no readable taxon: "
                + ", ".join(f"{k!r} x{v}" for k, v in self.unparsed.most_common()),
                file=sys.stderr,
            )
        if self.named and failed / self.named > MAX_UNPARSED_SHARE:
            raise RuntimeError(
                f"Penticton: {failed} of {self.named} GENUS/SPECIES values unreadable "
                f"(> {MAX_UNPARSED_SHARE:.0%}); has the layer changed shape?"
            )


if __name__ == "__main__":
    city = Penticton()
    pages = iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    )
    table = stream_to_table(pages, city.transform, keep=city.keep, label="Penticton OpenData")
    city.report()
    table = validate_coordinates(table, city="Penticton", city_code="CAPEN")
    table = enforce_tree_schema(table, city="Penticton", data_source="PENTICTON_OPENDATA")
    emit(table)

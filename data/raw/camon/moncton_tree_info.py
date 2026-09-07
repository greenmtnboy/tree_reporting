#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Moncton's city-owned tree inventory, from open.moncton.ca.

Source: "Trees" (`Trees/FeatureServer/0`) on the city's ArcGIS Online
organisation, 12,721 rows.  Paging, the freshness watermark and Esri's
epoch-milliseconds live in `_arcgis_shared`.

**`BOTNAME` is aliased "Botanical Name" and holds no botanical name.**  It is a
contraction -- `MapNor`, `LinLit`, `SprWhi`, `OakRed` -- three letters of each
word of the *inverted* English common name (Maple Norway, Linden Littleleaf,
Spruce White, Oak Red), 147 distinct values, and the layer publishes no domain
for it and no other species column.  Halifax has the same problem in miniature
(`ACRU`, `QURU` on ~1,000 rows) and drops those codes, on the grounds that
decoding a contraction is a guess at a taxon.  Here it is not 1,000 rows, it is
all of them, so the choice is between decoding and a city with no species at
all.

**The decoding is done in two blocks, and the difference between them is where
the expansion comes from.**

`CODES_FROM_OTTAWA` is mechanical.  Ottawa publishes its own species column as
a coded-value domain whose keys are inverted common names in exactly this style
-- `Maple Norway`, `Linden Littleleaf`, `Spruce White` -- and whose values are
the binomials.  A Moncton code expands to an Ottawa key when the code's
title-case segments prefix that key's words, in order, and **exactly one key
matches**; a code that matches none or several is left out.  That resolves 65
codes covering 9,588 of 12,697 rows without anyone reading a code, and it is
reproducible: point the rule at `coded_value_domain(<Ottawa layer>, 'SPECIES')`
and it produces this block again.

`CODES_READ_BY_HAND` is the residue, and it is what it says.  These are codes
whose expansion is legible but whose expanded name is not in Ottawa's domain to
confirm it -- `GinBib` is *Ginkgo biloba* spelled out rather than a common
name; `AM.Syc` is American Sycamore; `PlatExcl` is the London plane sold as
'Exclamation'; `AshMou` is a mountain ash of unrecorded species and resolves to
the genus.  A code I could not read confidently is **absent**, not guessed:
`OakCrim`, `CheMa`, `CheSte`, `CheBi` and `GolTre` are left to `Unknown` (the
last because the rule matched it to Ottawa's "Goldenchain tree" when "golden
rain tree", which Ottawa does not stock, prefixes it equally well).  Together
the two blocks cover about 90% of the layer's rows; the rest publish as
`Unknown`, which is the honest answer for a code nobody can expand.

**`Vac...` and `Stump` are the vacancy codes** -- 653 and 78 rows -- and
`UNITTYPE = 'NO TREE'` is another 19.  An empty planting site is not an
unidentified tree and is dropped, the rule `is_not_a_tree` applies everywhere
else.  `CITYTREE` and `PrivTree` are trees with no species recorded and stay.

**Diameter carries its own unit column, per row.**  `DIAMUOM` reads
`Centimeter` on 4,245 rows, `Inches` on 48, `Meters` on 4, and is missing or
the literal string `None` on the remaining 8,424.  The missing ones are read as
centimetres: every Canadian portal in this repo publishes centimetres, and
their median of 20 sits exactly where a municipal inventory's median diameter
sits, where reading them as inches would make Moncton's median tree 51 cm.

`GlobalID` is the key.  `UNITID` is aliased "Tree ID" and is *not* unique --
484 rows share an id across 64 values -- which is the `FACILITYID` trap from
EXTENDING.md in a column named even more convincingly.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, esri_ms_to_date, esri_point, iter_features
from _ingest_shared import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    parse_plant_date_year,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services1.arcgis.com/E26PuSoie2Y7bbyI/arcgis/rest/services/"
    "Trees/FeatureServer/0",
    timeout=300,
)

OUT_FIELDS = "GlobalID,BOTNAME,DIAM,DIAMUOM,UNITTYPE,INSTDATE,PlantedYear"

# Derived mechanically from Ottawa's `SPECIES` coded-value domain by the
# unique-prefix rule in the module docstring.  Ordered by row count.
CODES_FROM_OTTAWA: dict[str, str] = {
    "MapNor": "Acer platanoides",
    "MapRed": "Acer rubrum",
    "MapSil": "Acer saccharinum",
    "LilJap": "Syringa reticulata",
    "OakRed": "Quercus rubra",
    "LinLit": "Tilia cordata",
    "AshGre": "Fraxinus pennsylvanica",
    "MapSug": "Acer saccharum",
    "SprWhi": "Picea glauca",
    "AshWhi": "Fraxinus americana",
    "LocHon": "Gleditsia triacanthos",
    "Basswood": "Tilia americana",
    "OakBur": "Quercus macrocarpa",
    "OakEng": "Quercus robur",
    "MapFr": "Acer x freemanii",
    "BeeBlu": "Carpinus caroliniana",
    "Ser": "Amelanchier",
    "SprBlu": "Picea pungens",
    "OakPin": "Quercus palustris",
    "ElmAme": "Ulmus americana",
    "MapMan": "Acer negundo",
    "MapAmu": "Acer ginnala",
    "RedEas": "Cercis canadensis",
    "MapHed": "Acer campestre",
    "Hackberry": "Celtis occidentalis",
    "BirWhi": "Betula papyrifera",
    "HazTur": "Corylus colurna",
    "Ironwood": "Ostrya virginiana",
    "PinRed": "Pinus resinosa",
    "CatNo": "Catalpa speciosa",
    "SprBla": "Picea mariana",
    "PopLom": "Populus nigra",
    "FirBal": "Abies balsamea",
    "BeeEur": "Fagus sylvatica",
    "PinJac": "Pinus banksiana",
    "PinSco": "Pinus sylvestris",
    "LocBla": "Robinia pseudoacacia",
    "MapTat": "Acer tataricum",
    "AshEu": "Fraxinus excelsior",
    "WalBla": "Juglans nigra",
    "ElmChi": "Ulmus parvifolia",
    "LilCom": "Syringa vulgaris",
    "HicSha": "Carya ovata",
    "ElmSib": "Ulmus pumila",
    "HorCom": "Aesculus hippocastanum",
    "OakWhi": "Quercus alba",
    "Butternut": "Juglans cinerea",
    "AshBla": "Fraxinus nigra",
    "WilBla": "Salix nigra",
    "ChePin": "Prunus pensylvanica",
    "PinAus": "Pinus nigra",
    "FirWhi": "Abies concolor",
    "SprNor": "Picea abies",
    "BirRiv": "Betula nigra",
    "CheBla": "Prunus serotina",
    "PinMug": "Pinus mugo",
    "HicBit": "Carya cordiformis",
    "PopBal": "Populus balsamifera",
    "BirYel": "Betula alleghaniensis",
    "CheCho": "Prunus virginiana",
    "AldSpe": "Alnus",
    "BeeAme": "Fagus grandifolia",
    "MapJap": "Acer palmatum",
    "SprSerb": "Picea omorika",
    "BucOhi": "Aesculus glabra",
}

# Read by hand, because the expanded name is not in Ottawa's domain to confirm
# it.  A code whose expansion is a cultivar resolves to the taxon; one that
# names only a genus resolves to the genus.
CODES_READ_BY_HAND: dict[str, str] = {
    "GinBib": "Ginkgo biloba",        # the binomial itself, contracted
    "GinBibAg": "Ginkgo biloba",      # 'Autumn Gold'
    "AppCra": "Malus",                # Apple Crab
    "Apple": "Malus",
    "PinEas": "Pinus strobus",        # Pine Eastern (white)
    "AshMou": "Sorbus",               # Ash Mountain, species unrecorded
    "BirPap": "Betula papyrifera",    # Birch Paper
    "BirGre": "Betula populifolia",   # Birch Grey
    "HorEur": "Aesculus hippocastanum",   # Horsechestnut European
    "HorEurFas": "Aesculus hippocastanum",  # 'Fastigiata'
    "PlatExcl": "Platanus x hispanica",   # Platanus 'Exclamation'
    "PlaLon": "Platanus x hispanica",     # Plane London
    "AM.Syc": "Platanus occidentalis",    # American Sycamore
    "Amswtgum": "Liquidambar styraciflua",  # American sweetgum
    "KenCof": "Gymnocladus dioicus",      # Kentucky Coffeetree
    "KenCof.Esp": "Gymnocladus dioicus",  # 'Espresso'
    "BucYel": "Aesculus flava",       # Buckeye Yellow
    "CedWhi": "Thuja occidentalis",   # Cedar White
    "CedRed": "Juniperus virginiana",  # Cedar Red
    "PopTre": "Populus tremuloides",  # Poplar Trembling
    "PopLar": "Populus grandidentata",  # Poplar Largetooth
    "PopWhi": "Populus alba",         # Poplar White
    "EasCot": "Populus deltoides",    # Eastern Cottonwood
    "ElmAco": "Ulmus",                # Elm Accolade, an Asian hybrid cultivar
    "ElmAmPrinc": "Ulmus americana",  # Elm American 'Princeton'
    "ElmAmVF": "Ulmus americana",     # 'Valley Forge'
    "ElmEng": "Ulmus minor",          # Elm English
    "ElmCam": "Ulmus glabra",         # Elm Camperdown
    "MapBlkcol": "Acer nigrum",       # Maple Black, columnar
    "MapNorCri": "Acer platanoides",  # 'Crimson King'
    "MapNorGlo": "Acer platanoides",  # 'Globosum'
    "MapSyc": "Acer pseudoplatanus",  # Maple Sycamore
    "MapFrSg": "Acer x freemanii",    # 'Sienna Glen'
    "OakPyr": "Quercus robur",        # Oak Pyramidal, i.e. 'Fastigiata'
    "OakScar": "Quercus coccinea",    # Oak Scarlet
    "OakBla": "Quercus velutina",     # Oak Black
    "OakSwa": "Quercus bicolor",      # Oak Swamp white
    "HawEng": "Crataegus monogyna",   # Hawthorn English
    "CheFlo": "Prunus",               # Cherry Flowering, species unrecorded
    "HemEas": "Tsuga canadensis",     # Hemlock Eastern
    "KatJap": "Cercidiphyllum japonicum",  # Katsura Japanese
    "Larch": "Larix",
    "LinGreen": "Tilia cordata",      # Linden 'Greenspire'
    "LinCrim": "Tilia",               # Linden Crimean, unconfirmed species
    "SerCumulus": "Amelanchier",      # Serviceberry 'Cumulus'
    "SerSpFl": "Amelanchier",         # 'Spring Flurry'
    "SerxgRH": "Amelanchier",         # x grandiflora 'Robin Hill'
    "SuSh": "Rhus typhina",           # Sumac Staghorn
    "ZeSe": "Zelkova serrata",
    "Tui.Tree": "Liriodendron tulipifera",  # Tulip Tree
    "AmrCo": "Phellodendron amurense",  # Amur Cork
    "AldEur": "Alnus glutinosa",      # Alder European
    "CatWes": "Catalpa speciosa",     # Catalpa Western, i.e. the northern
    "DogRed": "Cornus sericea",       # Dogwood Red osier
    "GumBla": "Nyssa sylvatica",      # Gum Black
    "JapPa": "Styphnolobium japonicum",  # Japanese Pagoda
    "MagSau": "Magnolia x soulangeana",  # Magnolia Saucer
    "MulWhi": "Morus alba",           # Mulberry White
    "PeaCom": "Pyrus communis",       # Pear Common
    "ViOp": "Viburnum opulus",
    "WalEng": "Juglans regia",        # Walnut English
    "WilGol": "Salix alba",           # Willow Golden, i.e. 'Vitellina'
    "YewJap": "Taxus cuspidata",      # Yew Japanese
}

SPECIES_BY_CODE: dict[str, str] = {**CODES_FROM_OTTAWA, **CODES_READ_BY_HAND}

# Vacancy codes, in the spellings the layer uses: `Vac`, `VacCon`,
# `VacCredit`, `VacNewDev`, `VacASH`, `VacCancel`, `VacInfill`.  Matched by
# prefix so a new suffix is caught rather than published as a tree.
VACANCY_PREFIX = "vac"


def is_empty_site(code: str, unit_type: str) -> bool:
    """True for a record that is a planting site rather than a tree."""
    low = code.strip().lower()
    return (
        low.startswith(VACANCY_PREFIX)
        or low == "stump"
        or unit_type.strip().upper() == "NO TREE"
    )


def parse_dbh(value, unit) -> float | None:
    """A diameter to inches, honouring the row's own unit column.

    A missing unit is centimetres -- see the module docstring for why.
    """
    if value in (None, 0):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    unit_name = (unit or "").strip().lower()
    if unit_name == "inches":
        return number
    if unit_name == "meters":
        return cm_to_inches(number * 100)
    return cm_to_inches(number)


def iter_row_chunks():
    """One ArcGIS page at a time; this FeatureServer caps a page at 1000.

    The layer publishes no lat/lon columns and is stored in NAD83 New
    Brunswick Stereographic, so the geometry is requested and reprojected
    server-side.
    """
    return iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    )


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        geom = feature.get("geometry") or {}
        raw_id = str(rec.get("GlobalID") or "").strip()
        if not raw_id:
            continue
        code = str(rec.get("BOTNAME") or "").strip()
        if is_empty_site(code, str(rec.get("UNITTYPE") or "")):
            continue

        tree_id.append(f"mon-{raw_id}")
        species.append(SPECIES_BY_CODE.get(code))
        planted = esri_ms_to_date(rec.get("INSTDATE"))
        plant_date.append(planted or parse_plant_date_year(rec.get("PlantedYear")))
        lat, lon = esri_point(geom)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(parse_dbh(rec.get("DIAM"), rec.get("DIAMUOM")))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CAMON"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Moncton OpenData")
    table = validate_coordinates(table, city="Moncton", city_code="CAMON")
    table = enforce_tree_schema(table, city="Moncton", data_source="MONCTON_OPENDATA")
    emit(table)

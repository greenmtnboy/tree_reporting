#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Pinellas County, Florida's Public Works tree inventory, from the County's ArcGIS Online organisation.

LICENCE: no licence stated; displayed with attribution only, not licensed for
reuse.  The item's `licenseInfo` reads only "Data Certification: BRONZE"
(https://www.arcgis.com/home/item.html?id=4d5bed2b3a3c4985aa4ffb2c7670fd8b);
the County's open-data hub (https://new-Pinellas-eGIS.opendata.arcgis.com)
links the "Pinellas County Liability Disclaimer and User Agreement"
(https://pinellas.gov/pinellas-county-liability-disclaimer-and-user-agreement/),
which offers the data as public records under Florida Statutes, disclaims
warranty and forbids nothing but misuse of the system.

Source: "Pinellas County Tree Inventory" (ArcGIS item
4d5bed2b3a3c4985aa4ffb2c7670fd8b, owner `bcc104085@bcc.pinellas.gov` of the
County's own organisation `f5HgUpxURgEzTccH`, "Pinellas County eGIS"; data
steward Pinellas County Public Works), the service's only layer
`Pinellas_County_Tree_Inventory/FeatureServer/23`: 52,876 rows on
2026-11-27, every one `MAINTAINEDBY = Public Works` -- the trees the County's
Public Works department maintains along rights-of-way and on County
property, countywide (unincorporated Pinellas and County roads through the
municipalities).  `OWNEDBY` says whose land the tree stands on (Public Works
26,081, Unknown 17,887, Parks and Conservation Resources 6,200, Private
Entity 1,802, a handful of municipalities); it is not a filter, since every
row is a Public Works tree.  The layer is an i-Tree Eco data-model export
loaded in December 2022 (`editingInfo.dataLastEditDate` 2022-12-13) and not
edited since; the County's own server (egis.pinellas.gov) publishes no
public tree layer (`AGO/DEI_InfrastructureOperations` is stopped and the
`Trees` folder is empty), and the VHB-owned copy of this service is a
contractor's mirror of the same rows.  Geometry is NAD83; read in WGS84
(`outSR=4326`).

**The id is `GLOBALID`** (`pinellas-{guid}`): non-null on all 52,876 rows,
and the same GUIDs appear in the VHB mirror, so they survive a republish.
`FACILITYID` (`TREE-73302`) is the County's asset number and is filled on
every row too, but a GlobalID ranks first.  A row with no GlobalID is
dropped with a logged count; a repeated one raises.

**Species is the `SPECIES` code**, an i-Tree Eco species code (`QUVI`,
`QULA2`, `SAPA`) whose coded-value domain gives only an English common name.
The code is resolved by `SPECIES_CODES` below, written for every code the
layer uses: the common names are ambiguous where it matters (`Turkey oak` is
i-Tree's `QULA1`, *Quercus laevis*, which a common-name lookup reads as the
European *Quercus cerris*; `royal palm spp` and `date palm spp` are genera).
A code missing from the table falls back to the shared common-name resolver
on the domain's name, and one that still does not resolve counts as
unreadable; above 1% of named rows the build is refused.  A blank code
(10,016 rows, 9,591 of them with no DBH or height either) and `OTHER` ("To be
identified", 825) publish as `Unknown`: the layer has no status or site-type
column marking any row as something other than a tree.

**DBH is `DBH`, inches** (median 15; spikes at 4, 9, 15, 21, 27 and 33 are
six-inch size-class midpoints, recorded as a class rather than a tape
measurement); -1 (13,761 rows) and 0 are unmeasured and nulled.
`HEIGHT_TOTAL` is total height in feet (-1 when unmeasured); the canonical
schema has no recorded-height column, so it is not emitted.  No planting
date and no crown spread are recorded.
"""

import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (  # noqa: E402
    FeatureLayer,
    coded_value_domain,
    esri_point,
    iter_features,
)
from shared.ingest import (  # noqa: E402
    UNKNOWN_SPECIES,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)
from shared.species.english import species_from_common_name  # noqa: E402

SOURCE_URL = (
    "https://services.arcgis.com/f5HgUpxURgEzTccH/arcgis/rest/services/"
    "Pinellas_County_Tree_Inventory/FeatureServer/23"
)
LAYER = FeatureLayer(SOURCE_URL, timeout=180)
OUT_FIELDS = "OBJECTID,GLOBALID,SPECIES,DBH"
ID_PREFIX = "pinellas"

MAX_UNPARSED_SHARE = 0.01

# i-Tree Eco species code -> the binomial (or genus, for an `spp` code).
# Every code the layer used on 2026-11-27; the domain's English name is in the
# comment.  Values are names `sanitize_species` keeps as written or folds
# through the shared synonym table.
SPECIES_CODES: dict[str, str] = {
    "OTHER": UNKNOWN_SPECIES,  # Other (To be identified)
    "ACAC2": "Acrocomia aculeata",  # coyoli palm
    "ACRU": "Acer rubrum",  # Red maple
    "ACSP2": "Acacia",  # acacia spp
    "ACWR": "Acoelorrhaphe wrightii",  # Paurotis palm
    "ALJU": "Albizia julibrissin",  # Mimosa
    "ANIM": "Andira inermis",  # Cabbagebark tree
    "ARAL": "Archontophoenix alexandrae",  # Alexandra palm
    "ARAR": "Araucaria araucana",  # Monkeypuzzle tree
    "ARCU": "Archontophoenix cunninghamiana",  # King palm
    "ARHE": "Araucaria heterophylla",  # Norfolk island pine
    "ARHE2": "Artocarpus heterophyllus",  # Jackfruit
    "AVCA": "Averrhoa carambola",  # Star fruit
    "BAHO3": "Bauhinia hookeri",  # Queensland ebony
    "BAPU": "Bauhinia purpurea",  # Orchid tree
    "BERE": "Beaucarnea recurvata",  # Ponytail palm
    "BINO": "Bismarckia nobilis",  # Bismarckia palm
    "BRAC": "Schefflera actinophylla",  # Schefflera
    "BRPA": "Broussonetia papyrifera",  # Paper mulberry
    "BUCA": "Butia capitata",  # Jelly palm
    "CA1": "Carya",  # hickory spp
    "CACI": "Melaleuca citrina",  # Crimson bottlebrush
    "CAEQ": "Casuarina equisetifolia",  # Australian pine
    "CAGL": "Carya glabra",  # Pignut hickory
    "CAIL": "Carya illinoinensis",  # Pecan
    "CAIN4": "Calophyllum inophyllum",  # Alexandrian laurel
    "CAJA9": "Camellia japonica",  # Camellia
    "CAOV": "Carya ovata",  # Shagbark hickory
    "CAPA3": "Carica papaya",  # Papaya
    "CAVI": "Melaleuca viminalis",  # Weeping bottlebrush
    "CE2": "Celtis",  # hackberry spp
    "CECA": "Cercis canadensis",  # Eastern redbud
    "CELA": "Celtis laevigata",  # Sugarberry
    "CHHU": "Chamaerops humilis",  # Mediterranean fan palm
    "CHLU": "Dypsis lutescens",  # Areca palm
    "CICA": "Cinnamomum camphora",  # Camphor tree
    "CILI": "Citrus x limon",  # Lemon
    "CISI": "Citrus x sinensis",  # Orange
    "CISP": "Citrus",  # citrus spp
    "CO1": "Cornus",  # dogwood spp
    "CONU": "Cocos nucifera",  # Coconut palm
    "COUV": "Coccoloba uvifera",  # Sea grape
    "CRJA": "Cryptomeria japonica",  # Japanese red cedar
    "CUAN": "Cupaniopsis anacardioides",  # Carrotwood
    "CUSE": "Cupressus sempervirens",  # Italian cypress
    "CYCI": "Cycas circinalis",  # Queen sago
    "CYRE11": "Cycas revoluta",  # Sago palm
    "DA2": "Dalbergia",  # Indian rosewood spp
    "DASI": "Dalbergia sissoo",  # India rosewood
    "DERE": "Delonix regia",  # Royal poinciana
    "DR": "Dracaena",  # dracaena spp
    "DYDE2": "Dypsis decaryi",  # Triangle palm
    "ELDE": "Elaeocarpus decipiens",  # Japanese blueberry tree
    "ENCY": "Enterolobium cyclocarpum",  # Ear tree
    "ERCR": "Erythrina crista-galli",  # Cockspur coral tree
    "ERJA": "Eriobotrya japonica",  # Loquat tree
    "EU1": "Eucalyptus",  # gum spp
    "EUCI": "Eucalyptus cinerea",  # Silver dollar eucalyptus
    "EURO": "Eucalyptus robusta",  # Beakpod euclayptus
    "EUUN2": "Eugenia uniflora",  # Surinam cherry
    "FI1": "Ficus",  # fig spp
    "FIBE": "Ficus benjamina",  # Benjamin fig
    "FIBE2": "Ficus benghalensis",  # Indian banyan
    "FICA": "Ficus carica",  # Common fig
    "FIEL": "Ficus elastica",  # Rubber plant
    "GA2": "Gardenia",  # gardenia spp
    "GIBI": "Ginkgo biloba",  # Ginkgo
    "GRRO": "Grevillea robusta",  # Silk oak
    "GUOF": "Guaiacum officinale",  # Lignum-vitae
    "HISY": "Hibiscus syriacus",  # Rose-of-sharon
    "HITI": "Hibiscus tiliaceus",  # Sea hibiscus
    "HYVE9": "Hyophorbe verschaffeltii",  # Spindle palm
    "ILCA": "Ilex cassine",  # Dahoon
    "ILOP": "Ilex opaca",  # American holly
    "ILSP": "Ilex",  # holly spp
    "ILVO": "Ilex vomitoria",  # Yaupon
    "JAMI": "Jacaranda mimosifolia",  # Jacaranda
    "JU": "Juniperus",  # juniper spp
    "JUSI": "Juniperus virginiana",  # Southern redcedar (var. silicicola)
    "JUVI": "Juniperus virginiana",  # Eastern red cedar
    "KOPA": "Koelreuteria paniculata",  # Goldenrain tree
    "LA6": "Lagerstroemia",  # lagerstroemia spp
    "LAIN": "Lagerstroemia indica",  # Common crapemyrtle
    "LASP": "Lagerstroemia speciosa",  # Queens crapemyrtle
    "LELE": "Leucaena leucocephala",  # White lead tree
    "LICH": "Livistona chinensis",  # Chinese fan palm
    "LICH4": "Litchi chinensis",  # Lychee
    "LIJA": "Ligustrum japonicum",  # Ligustro
    "LIOR": "Liquidambar orientalis",  # Oriental sweetgum
    "LISP": "Ligustrum",  # privet spp
    "LIST": "Liquidambar styraciflua",  # Sweetgum
    "MA1": "Magnolia",  # magnolia spp
    "MAGR": "Magnolia grandiflora",  # Southern magnolia
    "MAIN": "Mangifera indica",  # Mango
    "MAVI": "Magnolia virginiana",  # Sweetbay
    "MEAZ": "Melia azedarach",  # Chinaberry
    "MEQU": "Melaleuca quinquenervia",  # Punk tree
    "MO": "Morus",  # mulberry spp
    "MONI": "Morus nigra",  # Black mulberry
    "MORU": "Morus rubra",  # Red mulberry
    "MU5": "Musa",  # banana spp
    "NEOL": "Nerium oleander",  # Oleander
    "NYSY": "Nyssa sylvatica",  # Black tupelo
    "PATO": "Paulownia tomentosa",  # Royal paulownia
    "PEAM": "Persea americana",  # Avocado
    "PEBO": "Persea borbonia",  # Redbay
    "PH7": "Phoenix",  # date palm spp
    "PHCA": "Phoenix canariensis",  # Canary island date palm
    "PHDA4": "Phoenix dactylifera",  # Date palm
    "PHRE": "Phoenix reclinata",  # Senegal date palm
    "PHRO": "Phoenix roebelenii",  # Pygmy date palm
    "PI2": "Pinus",  # pine spp
    "PICL": "Pinus clausa",  # Sand pine
    "PIDI3": "Pimenta dioica",  # Allspice
    "PIEL": "Pinus elliottii",  # Slash pine
    "PINI": "Pinus nigra",  # Austrian pine
    "PIPA": "Pinus palustris",  # Longleaf pine
    "PITA": "Pinus taeda",  # Loblolly pine
    "PL13": "Plumeria",  # plumeria spp
    "PLOC": "Platanus occidentalis",  # American sycamore
    "POMA": "Podocarpus macrophyllus",  # Yew podocarpus
    "PR": "Prunus",  # plum spp
    "PRAM": "Prunus americana",  # American plum
    "PRAN": "Prunus angustifolia",  # Chickasaw plum
    "PRCA": "Prunus caroliniana",  # Carolina laurelcherry
    "PRSE1": "Prunus serotina",  # Black cherry
    "QU": "Quercus",  # oak spp
    "QUAL": "Quercus alba",  # White oak
    "QUBI": "Quercus bicolor",  # Swamp white oak
    "QUCI": "Quercus incana",  # Bluejack oak
    "QUFA": "Quercus falcata",  # Southern red oak
    "QULA": "Quercus laceyi",  # Lacey oak
    "QULA1": "Quercus laevis",  # Turkey oak
    "QULA2": "Quercus laurifolia",  # Laurel oak
    "QUMI": "Quercus michauxii",  # Swamp chestnut oak
    "QUNI": "Quercus nigra",  # Water oak
    "QUSH": "Quercus shumardii",  # Shumard oak
    "QUVI": "Quercus virginiana",  # Live oak
    "R09": "Roystonea",  # royal palm spp
    "RAMA": "Ravenala madagascariensis",  # Traveler's tree
    "SA": "Salix",  # willow spp
    "SABA": "Salix babylonica",  # Weeping willow
    "SAPA": "Sabal palmetto",  # Cabbage palmetto
    "SCTE": "Schinus terebinthifolia",  # Brazilian pepper
    "SERE2": "Serenoa repens",  # Saw palmetto
    "SESU4": "Senna surattensis",  # Glossy shower
    "SWMA": "Swietenia mahagoni",  # West indian mahogany
    "SYCO": "Syagrus coronata",  # Licuri palm
    "SYJA": "Syzygium jambos",  # Malabar plum
    "SYRO": "Syagrus romanzoffiana",  # Queen Palm
    "TAAS": "Taxodium ascendens",  # Pond cypress
    "TADI": "Taxodium distichum",  # Baldcypress
    "TAIM": "Handroanthus impetiginosus",  # Pink trumpet tree
    "TASP": "Tabebuia",  # trumpet-tree spp
    # i-Tree's TH9 is Thuja, but the domain names it "red cedar spp" and Thuja
    # does not grow in Pinellas's climate: these are the Gulf coast's red
    # cedars, Juniperus virginiana (var. silicicola), read at genus rank.
    "TH9": "Juniperus",  # red cedar spp
    "THOR": "Platycladus orientalis",  # Oriental arborvitae
    "TICO": "Tilia cordata",  # Littleleaf linden
    "TRSE6": "Triadica sebifera",  # Tallowtree
    "ULAL": "Ulmus alata",  # Winged elm
    "ULAM": "Ulmus americana",  # American elm
    "ULPA": "Ulmus parvifolia",  # Chinese elm
    "ULRU": "Ulmus rubra",  # Slippery elm
    "ULS": "Ulmus",  # elm spp
    "VEME": "Adonidia merrillii",  # Christmas palm
    "VIOD": "Viburnum odoratissimum",  # Sweet viburnum
    "VISP2": "Viburnum",  # viburnum spp
    "WAFI": "Washingtonia filifera",  # California palm
    "WARO": "Washingtonia robusta",  # Mexican fan palm
    "WOBI2": "Wodyetia bifurcata",  # Foxtail palm
    "YU1": "Yucca",  # yucca spp
}


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("\xa0", " ").split())
    return text or None


class Counts:
    no_id = 0
    no_geometry = 0
    named = 0
    fallback: Counter = Counter()
    unparsed: Counter = Counter()


COUNTS = Counts()
DOMAIN: dict[str, str] = {}
SEEN: set[str] = set()


def guid(value) -> str | None:
    text = clean(value)
    return text.strip("{}").lower() if text else None


def keep(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    if guid(attrs.get("GLOBALID")) is None:
        COUNTS.no_id += 1
        return False
    lat, lon = esri_point(feature.get("geometry"))
    if lat is None or lon is None:
        COUNTS.no_geometry += 1
        return False
    return True


def parse_species(raw) -> str | None:
    """The binomial for an i-Tree species code.

    Examples:
        "QUVI"  -> "Quercus virginiana"
        "R09"   -> "Roystonea"
        "OTHER" -> "Unknown"
        " "     -> None
    """
    code = clean(raw)
    if code is None:
        return None
    COUNTS.named += 1
    code = code.upper()
    resolved = SPECIES_CODES.get(code)
    if resolved is not None:
        return resolved
    name = DOMAIN.get(code)
    resolved = species_from_common_name(name)
    if resolved is not None:
        COUNTS.fallback[f"{code} ({name}) -> {resolved}"] += 1
        return resolved
    COUNTS.unparsed[f"{code} ({name})"] += 1
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
    planted: list[date | None] = []
    latitude: list[float] = []
    longitude: list[float] = []
    dbh: list[float | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        key = guid(attrs["GLOBALID"])
        if key in SEEN:
            raise RuntimeError(f"Pinellas County ingest: GLOBALID {key} is repeated")
        SEEN.add(key)
        lat, lon = esri_point(feature.get("geometry"))
        tree_id.append(f"{ID_PREFIX}-{key}")
        species.append(parse_species(attrs.get("SPECIES")))
        planted.append(None)
        latitude.append(lat)
        longitude.append(lon)
        diameter = attrs.get("DBH")
        dbh.append(float(diameter) if diameter is not None and diameter > 0 else None)

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["USPIE"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "plant_date": pa.array(planted, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


if __name__ == "__main__":
    DOMAIN.update({code.upper(): name for code, name in coded_value_domain(LAYER, "SPECIES").items()})
    table = stream_to_table(iter_chunks(), transform, keep=keep, label="Pinellas County OpenData")
    print(
        f"Pinellas County ingest: dropped {COUNTS.no_id} rows with no GLOBALID, "
        f"{COUNTS.no_geometry} with no geometry",
        file=sys.stderr,
    )
    if COUNTS.fallback:
        print(
            "Pinellas County ingest: species codes not in SPECIES_CODES, resolved "
            f"by common name: {COUNTS.fallback.most_common()}",
            file=sys.stderr,
        )
    unparsed = sum(COUNTS.unparsed.values())
    if unparsed:
        print(
            f"Pinellas County ingest: {unparsed} species codes unreadable: "
            f"{COUNTS.unparsed.most_common(10)}",
            file=sys.stderr,
        )
    if COUNTS.named and unparsed / COUNTS.named > MAX_UNPARSED_SHARE:
        raise RuntimeError(
            f"Pinellas County ingest: {unparsed} of {COUNTS.named} species codes "
            "unreadable; the SPECIES domain has changed"
        )
    table = validate_coordinates(table, city="Pinellas County", city_code="USPIE")
    table = enforce_tree_schema(
        table, city="Pinellas County", data_source="PINELLAS_COUNTY_OPENDATA"
    )
    emit(table)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Hastings District, NZ's council tree inventory.

Source: "Hastings DC Trees" (ArcGIS item 11ae2eca83e54f6caeaa0d470b4a7ab1,
owner `hdc10`, Hastings District Council's ArcGIS Online org `HDCGIS`), the
hosted layer `Hastings_DC_Trees/FeatureServer/0`: "Location of Trees owned
and maintained by the Hastings District Council. Includes road side and park
trees. Last Updated September 2025".  17,442 rows on 2026-11-21, stored in
NZTM (EPSG:2193) and read reprojected to WGS84 (`outSR=4326`).  The item is
in the council's open data hub catalogue (group "HDC Open Data Community",
https://hastings-dc-open-data-hdcgis.hub.arcgis.com/).

Licence: the item's own licence field is empty.  The council's maps page
says of that portal "The data, which includes underground services,
kerb-lines, District Plan zones and contours, is available for use under a
Creative Commons Licence" (https://www.hastingsdc.govt.nz/services/maps-and-gis),
and the council's other hub items state "licensed under the Creative Commons
Attribution 4.0 BY licence. All work using this data must attribute the work
to Hastings DC."  Attribute to Hastings District Council.

Every row is the council's (`asset_owner` = Parks & Reserves, 17,424, or
Water Services, 18); Napier City's trees are not in the layer, and none of
its points falls inside Napier.  `tree_site` is Street Tree (10,971) or Park
Tree (6,471).

**The id is the rounded coordinate (`hastings-{lon:.7f}_{lat:.7f}`)**, as
for Spokane (EXTENDING.md, step 1).  The layer has no GlobalID and no asset
id: its fields are `tree_species`, `tree_site`, `common_name`, `asset_owner`
and `edible_tree`.  OBJECTID runs 1..17,442 without a gap and the layer
carries one edit stamp (2025-09-17), what a wholesale reload looks like.
Rows sharing a 7 dp point are dropped (`drop_stacked_coordinates`) unless
they agree on species and cultivar, when one is kept: in November 2026 that
was one point holding 23 different trees (a default position in Hastings,
all dropped), and four points repeating one tree (an Acer palmatum, and
three points of unnamed rows), each kept once.

**Which rows are trees.**  The layer has no status column.  The one
`MISSING TREE` row ("Place for tree but no tree") is an empty site and is
dropped.  `Unknown` (832, common name "IDK") and the 206 rows with no species
at all are kept as unidentified trees.

**Species.**  `tree_species` is free text with no domain: the scientific
name with the cultivar usually written unquoted after it (`Prunus serrulata
Kanzan`, `Ulmus procera Louie van Houtte`), sometimes in place of the
epithet (`Malus Red Top`, `Prunus Mount Fuji`), sometimes quoted.  The split
(`species_and_cultivar`): a capitalised second word is a cultivar unless it
is one of the epithets the layer happens to capitalise
(`CAPITALISED_EPITHETS`, `Quercus Ilex`); every word after the epithet is
the cultivar.  `NAME_FIXES` are the names that reading gets wrong: NZ
cherry and olive cultivars written as if epithets (`Prunus awanui` is the
cherry 'Awanui', `Olea barnea` the olive 'Barnea'), a garbled epithet the
shared misspelling audit cannot pair (`Prunus cerastrium Nigra`), a genus
swapped (`Hesperocyparis sempervirens`), and a quoted cultivar holding an
apostrophe.  Misspelt epithets are left to the shared misspelling map.  The
`common_name` column is not mapped.

**Not published.**  The layer records no trunk size, planting date, height
or crown spread, so `diameter_at_breast_height` and `plant_date` are null;
`edible_tree` is not mapped.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    coordinate_tree_id,
    drop_stacked_coordinates,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services1.arcgis.com/8L3DQUzjrkgEmDpQ/arcgis/rest/services/"
    "Hastings_DC_Trees/FeatureServer/0",
    timeout=180,
)
FIELDS = "OBJECTID,tree_species"
ID_PREFIX = "hastings"

# `tree_species` values that record an empty site, not a tree.
NOT_A_TREE_NAMES = frozenset({"missing tree"})

# Second words the layer capitalises that are real epithets, not cultivars.
CAPITALISED_EPITHETS = frozenset({
    "baccata", "campbelli", "cunninghamiana", "doltsopa", "europaea", "ilex",
    "lucens", "persica", "phoenicea",
})
# A rank placeholder in the epithet's place: the genus only.
GENUS_ONLY = frozenset({"spp", "spp.", "sp", "sp."})
# Infraspecific rank words: what follows is a botanical rank, not a cultivar.
RANK_WORDS = frozenset({"var", "var.", "subsp", "subsp.", "ssp", "ssp.", "f.", "forma"})

# Raw names (whitespace collapsed) the reading above gets wrong, mapped to
# (species, cultivar).
NAME_FIXES: dict[str, tuple[str, str | None]] = {
    # Cultivars written as if they were the epithet.
    "Prunus awanui": ("Prunus", "Awanui"),
    "Prunus accolade": ("Prunus", "Accolade"),
    "Prunus felix jury": ("Prunus", "Felix Jury"),
    "Malus profusion": ("Malus", "Profusion"),
    "Malus van Eseltine": ("Malus", "Van Eseltine"),
    "Malus ballerina": ("Malus", "Ballerina"),
    "Olea barnea": ("Olea europaea", "Barnea"),
    "Olea sorani": ("Olea europaea", "Sorani"),
    "Olea verdale": ("Olea europaea", "Verdale"),
    "Cornus white wonder": ("Cornus", "White Wonder"),
    "Cornus national": ("Cornus", "National"),
    "Metrosideros varigated": ("Metrosideros", None),
    "Juniperus Kaizuka": ("Juniperus chinensis", "Kaizuka"),
    # 'Pissardii' and 'Nigra' are cherry-plum cultivars; `cerastrium` is a
    # garble of cerasifera too far for the shared misspelling audit to pair.
    "Prunus pissardii Nigra": ("Prunus cerasifera", "Nigra"),
    "Prunus cerastrium Nigra": ("Prunus cerasifera", "Nigra"),
    "Metasequioa glypstrobiodies": ("Metasequoia glyptostroboides", None),
    "Vaccinium oxyciccis": ("Vaccinium oxycoccos", None),
    "Prunus serr Shimidsu Sakura": ("Prunus serrulata", "Shimidsu Sakura"),
    # The Hillier cherry 'Spire' (Prunus x hillieri), not a serrulata.
    "Prunus serrulata Hilliera Spire": ("Prunus x hillieri", "Spire"),
    "Populus nigra var italica": ("Populus nigra", "Italica"),
    "Populus nigra 'Cultivar'": ("Populus nigra", None),
    "Elaeocarpus dentatus var. dentatus": ("Elaeocarpus dentatus", None),
    # Kahikatea ("White pine") under its old genus with a misspelt epithet.
    "Podocarpus dacrydiodes kileka": ("Dacrycarpus dacrydioides", None),
    # The variegated pohutukawa, by its common name.
    "Pohutakawa variegated": ("Metrosideros excelsa", None),
    # Italian cypress filed under the American cypresses' genus.
    "Hesperocyparis sempervirens": ("Cupressus sempervirens", None),
    "Hesperocyparis sempervirens 'Swanes Gold'": ("Cupressus sempervirens", "Swane's Gold"),
    # Quoted cultivars an apostrophe or a typo would cut short.
    "Cupressus sempervirens 'Swane's golden'": ("Cupressus sempervirens", "Swane's Golden"),
    "Crataegus oxyacantha 'Paul's Scarlet'": ("Crataegus oxyacantha", "Paul's Scarlet"),
    "Cedrela sinensis ''Flamingo": ("Cedrela sinensis", "Flamingo"),
    "Cupressus arizonica 'glauea'": ("Cupressus arizonica", "Glauca"),
    "Quercus robur 'Fastigata'": ("Quercus robur", "Fastigiata"),
}


def clean(value) -> str | None:
    if value is None:
        return None
    # One row carries a non-breaking space mis-decoded as "Â\xa0"
    # (`Ulmus glabraÂ 'Camperdownii'`).
    text = str(value).replace("Â ", " ").replace("�", " ")
    text = " ".join(text.replace("\xa0", " ").split())
    return text or None


def _cultivar_words(words: list[str]) -> str | None:
    if not words:
        return None
    text = " ".join(words)
    if text == text.lower():
        text = " ".join(w[:1].upper() + w[1:] for w in words)
    return text


def species_and_cultivar(raw: str | None) -> tuple[str | None, str | None]:
    """Split `tree_species` into (species, cultivar); see the module docstring.

    Examples:
        "Prunus serrulata Kanzan"         -> ("Prunus serrulata", "Kanzan")
        "Prunus cerasifera nigra"         -> ("Prunus cerasifera", "Nigra")
        "Malus Red Top"                   -> ("Malus", "Red Top")
        "Quercus Ilex"                    -> ("Quercus Ilex", None)
        "Prunus Spp"                      -> ("Prunus", None)
        "Magnolia 'Galaxy'"               -> ("Magnolia 'Galaxy'", None)  (schema splits)
        "Fagus sylvatica (mixed)"         -> ("Fagus sylvatica", None)
    """
    name = clean(raw)
    if name is None:
        return None, None
    if name in NAME_FIXES:
        return NAME_FIXES[name]
    if any(q in name for q in "'\"‘’“”"):
        # A quoted cultivar: `enforce_tree_schema` extracts it.
        return name, None
    words = [w for w in name.split() if not (w.startswith("(") and w.endswith(")"))]
    if len(words) < 2:
        return " ".join(words) or None, None
    genus, second, rest = words[0], words[1], words[2:]
    if second.lower() in GENUS_ONLY:
        return genus, None
    if second[:1].isupper() and second.lower() not in CAPITALISED_EPITHETS:
        return genus, _cultivar_words(words[1:])
    if rest and rest[0].lower() in RANK_WORDS:
        return f"{genus} {second}", None
    return f"{genus} {second}", _cultivar_words(rest)


class Counts:
    no_geometry = 0
    empty_site = 0


COUNTS = Counts()


def keep(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    if (clean(attrs.get("tree_species")) or "").lower() in NOT_A_TREE_NAMES:
        COUNTS.empty_site += 1
        return False
    lat, lon = esri_point(feature.get("geometry"))
    if lat is None or lon is None:
        COUNTS.no_geometry += 1
        return False
    return True


def iter_chunks():
    yield from iter_features(
        LAYER,
        out_fields=FIELDS,
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

    for feature in features:
        attrs = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        name, cv = species_and_cultivar(attrs.get("tree_species"))
        tree_id.append(coordinate_tree_id(ID_PREFIX, lon, lat))
        species.append(name)
        cultivar.append(cv)
        latitude.append(lat)
        longitude.append(lon)

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["NZHAS"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "cultivar": pa.array(cultivar, type=pa.string()),
        "plant_date": pa.array([None] * n, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array([None] * n, type=pa.float64()),
    })


if __name__ == "__main__":
    table = stream_to_table(iter_chunks(), transform, keep=keep, label="Hastings OpenData")
    print(
        f"Hastings ingest: dropped {COUNTS.no_geometry} rows with no geometry, "
        f"{COUNTS.empty_site} empty sites (MISSING TREE)",
        file=sys.stderr,
    )
    table = drop_stacked_coordinates(
        table, label="Hastings OpenData", identical=("species", "cultivar")
    )
    table = validate_coordinates(table, city="Hastings", city_code="NZHAS")
    table = enforce_tree_schema(
        table, city="Hastings", data_source="HASTINGS_OPENDATA"
    )
    emit(table)

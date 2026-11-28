#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Beaverton, Oregon's tree inventory.

The City's `Tree_Inventory_Hosted` feature layer on its own ArcGIS server
(`gisweb.beavertonoregon.gov`, `Hosted` folder): about 31,000 street,
landscape and significant trees.  It answers anonymously, but no public
catalogue item points at it (the City's "Beaverton Tree Inventory Web Map"
shows the 2009 significant-trees layer instead).  No licence is stated:
displayed with attribution only, not licensed for reuse.

* **Only `status = 2` (Existing) is a tree.**  The domain also holds Planned,
  Abandoned, Removed and Proposed; ~2,400 Planned rows are planting sites.
* **There is no scientific name.**  `treedescrip` is a free-text common or
  cultivar name (995 spellings: `Flowering pear`, `Red Sunset Maple`,
  `Summit `, blank on ~13,000 rows) and `treegroup` a coded genus-level
  common name (`MAPLE`, `LINDEN` ...).  The species is resolved in order:
  1. `species_from_common_name` (the shared table) on the description;
  2. `CULTIVAR_NAMES` here, keyed by the description *and* the group,
     because Beaverton writes the cultivar without the tree (`Summit` under
     ASH is Fraxinus pennsylvanica; `red` under MAPLE is Acer rubrum);
  3. a description that is itself a binomial of the group's genus;
  4. the group's genus (`GROUP_GENUS`), or None (published as Unknown) for a
     group that names more than one genus here (CEDAR, FIR, CYPRESS,
     LOCUST, REDWOOD, SEQUOIA, LAUREL, MYRTLE, OTHER).
* `diameter` is DBH in inches (0 is unrecorded); `yearplanted` becomes that
  year's 1 January (~4,000 rows).
* The layer is in Oregon State Plane North; `out_sr` reprojects it.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features  # noqa: E402
from shared.ingest import (  # noqa: E402
    emit,
    enforce_tree_schema,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)
from shared.species.english import common_name_key, species_from_common_name  # noqa: E402

LAYER = FeatureLayer(
    "https://gisweb.beavertonoregon.gov/server/rest/services/Hosted/Tree_Inventory_Hosted/FeatureServer/0"
)
OUT_FIELDS = "objectid,status,treegroup,treedescrip,diameter,yearplanted"
EXISTING = 2

# `treegroup` -> the genus (or species, where the group is one) it names.
GROUP_GENUS: dict[str, str | None] = {
    "APPLE": "Malus", "ARBORVITAE": "Thuja", "ASH": "Fraxinus", "ASPEN": "Populus",
    "BEECH": "Fagus", "BIRCH": "Betula", "BOXELDER": "Acer negundo",
    "CASCARA": "Frangula purshiana", "CATALPA": "Catalpa", "CEDAR": None,
    "CHERRY": "Prunus", "CHESTNUT": "Castanea", "CHITALPA": None,
    "COFFEETREE": "Gymnocladus dioicus", "CORK TREE": "Phellodendron",
    "COTTONWOOD": "Populus", "CRABAPPLE": "Malus", "CRAPE MYRTLE": "Lagerstroemia",
    "CYPRESS": None, "DOGWOOD": "Cornus", "DOVE-TREE": "Davidia involucrata",
    "EASTERN REDBUD": "Cercis canadensis", "ELDERBERRY": "Sambucus", "ELM": "Ulmus",
    "FIG": "Ficus", "FILBERT": "Corylus", "FIR": None, "FRINGETREE": "Chionanthus",
    "GINKGO": "Ginkgo biloba", "GLORYBOWER": "Clerodendrum", "GOLDENRAIN": "Koelreuteria",
    "HACKBERRY": "Celtis", "HARDY RUBBER TREE": "Eucommia ulmoides", "HAWTHORN": "Crataegus",
    "HEMLOCK": "Tsuga", "HEPTACODIUM": "Heptacodium miconioides", "HOLLY": "Ilex",
    "HOPHORNBEAM": "Ostrya", "HORNBEAM": "Carpinus", "HORSECHESTNUT": "Aesculus",
    "KATSURA": "Cercidiphyllum japonicum", "LAUREL": None, "LINDEN": "Tilia",
    "LOCUST": None, "MAACKIA": "Maackia", "MADRONE": "Arbutus", "MAGNOLIA": "Magnolia",
    "MAPLE": "Acer", "MONKEY PUZZLE TREE": "Araucaria araucana", "MULBERRY": "Morus",
    "MYRTLE": None, "OAK": "Quercus", "OSAGE-ORANGE": "Maclura pomifera", "OTHER": None,
    "PALM": "Palm", "PARROTIA": "Parrotia persica", "PEAR": "Pyrus", "PERSIMMON": "Diospyros",
    "PHOTINIA": "Photinia", "PINE": "Pinus", "PISTACHE": "Pistacia", "PLUM": "Prunus",
    "POPLAR": "Populus", "PRIVET": "Ligustrum", "RAISINTREE": "Hovenia dulcis",
    "REDWOOD": None, "ROSE OF SHARON": "Hibiscus syriacus", "SEQUOIA": None,
    "SERVICEBERRY": "Amelanchier", "SILK TREE": "Albizia julibrissin", "SILVERBELL": "Halesia",
    "SMOKE TREE": "Cotinus", "SNOWBELL": "Styrax", "SOURWOOD": "Oxydendrum arboreum",
    "SPRUCE": "Picea", "STEWARTIA": "Stewartia", "SWEETGUM": "Liquidambar",
    "SYCAMORE": "Platanus", "TREE LILAC": "Syringa reticulata",
    "TREE OF HEAVEN": "Ailanthus altissima", "TULIPTREE": "Liriodendron tulipifera",
    "TUPELO": "Nyssa", "WALNUT": "Juglans", "WILLOW": "Salix",
    "YELLOWWOOD": "Cladrastis kentukea", "ZELKOVA": "Zelkova",
}

# (common_name_key(treedescrip), treegroup) -> species, for the descriptions
# the shared table does not resolve; the ~50 that cover most named rows.
# A hybrid cultivar (Pacific Sunset = A. truncatum x platanoides) is left to
# the genus fallback.
CULTIVAR_NAMES: dict[tuple[str, str], str] = {
    **{(k, "PEAR"): "Pyrus calleryana" for k in (
        "flowering pear", "bradford", "chanticleer pear", "chanticleer",
        "chanticleer flowering pear", "callery flowering", "capitol flowering pear")},
    ("summit", "ASH"): "Fraxinus pennsylvanica",
    **{(k, "MAPLE"): "Acer rubrum" for k in (
        "red sunset maple", "red sunset", "bowhall red maple", "bowhall", "red",
        "red maple discontinued", "acer rubrum")},
    ("armstrong red maple", "MAPLE"): "Acer x freemanii",
    ("norway", "MAPLE"): "Acer platanoides",
    ("paperbark", "MAPLE"): "Acer griseum",
    ("paper bark", "MAPLE"): "Acer griseum",
    ("rocky mountain maple", "MAPLE"): "Acer glabrum",
    ("silver", "MAPLE"): "Acer saccharinum",
    ("big leaf maple", "MAPLE"): "Acer macrophyllum",
    ("european globe ash", "ASH"): "Fraxinus excelsior",
    ("golden desert ash", "ASH"): "Fraxinus excelsior",
    **{(k, "ASH"): "Fraxinus angustifolia" for k in ("reywood ash", "reywood", "ray wood ash")},
    ("european", "HORNBEAM"): "Carpinus betulus",
    ("european pyramidal hornbeam", "HORNBEAM"): "Carpinus betulus",
    ("rising fire american hornbeam", "HORNBEAM"): "Carpinus caroliniana",
    ("oregon white oak", "OAK"): "Quercus garryana",
    ("pin", "OAK"): "Quercus palustris",
    ("pinoak", "OAK"): "Quercus palustris",
    ("white", "OAK"): "Quercus alba",
    ("skymaster oak", "OAK"): "Quercus robur",
    ("skyrocket oak", "OAK"): "Quercus robur",
    ("little leaf", "LINDEN"): "Tilia cordata",
    ("village green zelkova", "ZELKOVA"): "Zelkova serrata",
    ("green vase", "ZELKOVA"): "Zelkova serrata",
    ("western redcedar", "CEDAR"): "Thuja plicata",
    ("incense", "CEDAR"): "Calocedrus decurrens",
    ("akebono flowering cherry", "CHERRY"): "Prunus x yedoensis",
    ("princeton sentry", "GINKGO"): "Ginkgo biloba",
    ("autumn gold", "GINKGO"): "Ginkgo biloba",
    ("jacquemonti", "BIRCH"): "Betula utilis",
    ("white", "SPRUCE"): "Picea glauca",
    ("kousa", "DOGWOOD"): "Cornus kousa",
    ("afterburner black tupelo", "TUPELO"): "Nyssa sylvatica",
}


def species_name(description, group) -> str | None:
    group = (group or "").strip().upper()
    text = " ".join(str(description or "").split())
    if text:
        resolved = species_from_common_name(text)
        if resolved:
            return resolved
        resolved = CULTIVAR_NAMES.get((common_name_key(text), group))
        if resolved:
            return resolved
        genus = (GROUP_GENUS.get(group) or "").split(" ")[0]
        if genus and text.lower().startswith(genus.lower() + " "):
            return normalize_species(text)
    return GROUP_GENUS.get(group)


def iter_row_chunks():
    return iter_features(LAYER, out_fields=OUT_FIELDS, where=f"status = {EXISTING}", return_geometry=True, out_sr=4326, order_by="objectid")


def transform(features: list[dict]) -> pa.Table:
    ids, species, planted, lats, lons, dbhs = [], [], [], [], [], []
    for feature in features:
        rec = feature.get("attributes") or {}
        if rec.get("status") != EXISTING:
            continue
        lat, lon = esri_point(feature.get("geometry"))
        ids.append(f"bvn-{rec['objectid']}")
        species.append(species_name(rec.get("treedescrip"), rec.get("treegroup")))
        year = rec.get("yearplanted")
        planted.append(date(int(year), 1, 1) if year and 1800 <= int(year) <= date.today().year else None)
        lats.append(lat)
        lons.append(lon)
        dbh = rec.get("diameter")
        dbhs.append(float(dbh) if dbh not in (None, 0) else None)
    return pa.table(
        {
            "tree_id": pa.array(ids, type=pa.string()),
            "city": pa.array(["USBVN"] * len(ids), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array(planted, type=pa.date32()),
            "latitude": pa.array(lats, type=pa.float64()),
            "longitude": pa.array(lons, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbhs, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Beaverton tree inventory")
    table = validate_coordinates(table, city="Beaverton", city_code="USBVN")
    table = enforce_tree_schema(table, city="Beaverton", data_source="BEAVERTON")
    emit(table)

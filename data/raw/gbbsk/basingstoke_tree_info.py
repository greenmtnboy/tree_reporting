#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Basingstoke and Deane's council tree inventory, from the council's map server.

No licence stated; displayed with attribution only, not licensed for reuse.
The layer's portal item (`TreesInspections`, 3fc6e1e0bc09491fa67020e372988fc9
on gis.basingstoke.gov.uk/portal, "A group of layers for the Trees
Inspections app - for external use") has no licence or access information,
and the council's open-data page (basingstoke.gov.uk/opendata: "Our open data
is released under the Open Government Licence (version 3)") does not list
the tree inventory among its datasets.

Source: `AGOL/TreesInspections/FeatureServer/6` (`BDBC Trees`), "BDBC Point
Trees that are being update[d] from the Ezytreev WFS every night", stored in
British National Grid and read reprojected to WGS84 by the server (1.1-1.4 m
from pyproj's EPSG:27700 transform over a 19-row sample).  72,077 rows on
2026-11-21.  `Owner` says who manages the tree -- Amenity (council),
Woodlands, Highways (Hampshire County Council's highway trees), Property
Services, Cemeteries, Housing Association, HCC, Unknown -- and every owner is
read: they are all trees the council surveys.  Tree groups are a separate
polygon layer (10) and are not read.

**The layer is reloaded whole**: `OBJECTID` is a dense 1..72,077 and
`gml_id` is the WFS export's feature number.  **`TreeCode` is the key**
(`basingstoke-{TreeCode}`): the Ezytreev tree code, an integer from 4 to
990,000, non-null and distinct on all 72,077 rows (checked over the whole
layer on 2026-11-21).  The layer has no GlobalID.  A row without one is
dropped with a logged count.

**Felled trees stay in the layer** with `Felled` = `Yes` (18,723 rows on
2026-11-21) and are dropped.  `Hidden` and `Private` are null on every row.
`"Possible treeplanting site"` in `Species` (38 rows) is a site, not a tree,
and is dropped.  `Vitality` = `Dead` (486 standing trees) publishes as Dead.

**Species is `Species`**, a botanical name (`Common_Name` is a short English
name and is not mapped).  Quoted cultivars are split by `enforce_tree_schema`;
`TAXA` is the closed table of the names that are not a clean binomial with
an optional quoted cultivar: unquoted cultivars (`Prunus Okame`, `Sorbus aria
Magnifica`), capitalised epithets (`Sambucus Nigra`), and the placeholders
(`'Species not known'`, `'Species not in list'`, `Dead Tree - Unknown
Species`, `Various Hedge`).  Misspellings (`Salix babyloncia`, `Gleditisia
tricanthos`) are left to the shared species map.

**DBH is `Trunk`, a band of centimetres of diameter** (`16cm to 30cm`,
`31cm - 45cm` ... `Over 151cm`).  Diameter, not girth: median recorded
height rises from 5 m in the 1-15 cm band to 15 m at 46-60 cm and 20 m from
101 cm, and median spread from 3 m to 15 m, a diameter's allometry.  Each band
publishes its midpoint, the open `Over 151cm` its bound plus half the 25 cm
top width (163.5 cm), Ballarat's rule, divided by 2.54.  An unknown band label
counts as unreadable and above 1% the ingest refuses.

**Crown width is `Spread`**, metres of crown spread (median 6 m; 3 m in the
smallest trunk band, 15 m at 101-125 cm).  Zero or negative (1,375 standing
trees) and anything above 40 m (one row, 68 m on a 16 m pine) are null.
`Height` (metres) has no canonical column.  `Age` and `Age_Class` are bands,
not planting dates, so `plant_date` is null on every row.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    DEAD_SPECIES,
    SHRUB_SPECIES,
    UNKNOWN_SPECIES,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://gis.basingstoke.gov.uk/server/rest/services/AGOL/TreesInspections/FeatureServer/6",
    timeout=180,
)

OUT_FIELDS = "OBJECTID,TreeCode,Species,Felled,Vitality,Trunk,Spread"

NOT_A_TREE = frozenset({'"Possible treeplanting site"'})

# `Trunk` band (whitespace collapsed) -> diameter midpoint in centimetres.
TRUNK_BAND_CM: dict[str, float] = {
    "1cm to 15cm": 8.0,
    "16cm to 30cm": 23.0,
    "31cm - 45cm": 38.0,
    "46cm - 60cm": 53.0,
    "61cm to 75cm": 68.0,
    "76cm to 100cm": 88.0,
    "101cm to 125cm": 113.0,
    "126cm to 150cm": 138.0,
    "Over 151cm": 163.5,
}

MAX_SPREAD_M = 40.0

# Refuse when more than this share of `Trunk` labels cannot be read.
MAX_UNREADABLE_SHARE = 0.01

# `Species` (whitespace collapsed) -> (species, cultivar) for every name that
# is not a clean binomial with an optional quoted cultivar.
TAXA: dict[str, tuple[str, str | None]] = {
    # Placeholders.
    "'Species not known'": (UNKNOWN_SPECIES, None),
    "'Species not in list'": (UNKNOWN_SPECIES, None),
    "Various area": (UNKNOWN_SPECIES, None),
    "Various self set species": (UNKNOWN_SPECIES, None),
    "Various Hedge": (SHRUB_SPECIES, None),
    "Dead Tree - Unknown Species": (DEAD_SPECIES, None),
    # A cultivar note with no cultivar named.
    "Acer platanoides Purple Variety": ("Acer platanoides", None),
    "Acer pseudoplatanus cultivar": ("Acer pseudoplatanus", None),
    # Capitalised epithets.
    "Sambucus Nigra": ("Sambucus nigra", None),
    "Acer Pseudoplatanus Negenia": ("Acer pseudoplatanus", "Negenia"),
    "Prunus Maackii Amber Beauty": ("Prunus maackii", "Amber Beauty"),
    # Hybrids written without their mark.
    "Ulmus hollandica": ("Ulmus x hollandica", None),
    "Laburnum watereri": ("Laburnum x watereri", None),
    "Laburnum vossii": ("Laburnum x watereri", "Vossii"),
    # Unquoted cultivars after the genus.
    "Prunus Okame": ("Prunus", "Okame"),
    "Prunus Snow Goose": ("Prunus", "Snow Goose"),
    "Prunus Tai Haku": ("Prunus", "Tai Haku"),
    "Prunus Umineko": ("Prunus", "Umineko"),
    "Prunus `Accolade`": ("Prunus", "Accolade"),
    "prunus kordia": ("Prunus avium", "Kordia"),
    "Malus Evereste": ("Malus", "Evereste"),
    "Malus Rudolph": ("Malus", "Rudolph"),
    "Malus Coxs Orange Pippin": ("Malus domestica", "Cox's Orange Pippin"),
    "Malus Elstar": ("Malus domestica", "Elstar"),
    "ulmus dodoens": ("Ulmus", "Dodoens"),
    'Fraxinus "Jaspidea"': ("Fraxinus excelsior", "Jaspidea"),
    "Fagus pendula": ("Fagus sylvatica", "Pendula"),
    "Betula tristis": ("Betula pendula", "Tristis"),
    # Unquoted cultivars after the species.
    "Fraxinus oxycarpa Raywood": ("Fraxinus oxycarpa", "Raywood"),
    "Prunus x hillieri Spire": ("Prunus x hillieri", "Spire"),
    "Fagus sylvatica purpurea": ("Fagus sylvatica", "Purpurea"),
    "Ilex aquifolium argentea marginata": ("Ilex aquifolium", "Argentea Marginata"),
    "Sorbus aucuparia Golden Wonder": ("Sorbus aucuparia", "Golden Wonder"),
    "Cedrus atlantica glauca": ("Cedrus atlantica", "Glauca"),
    "Betula pendula youngii": ("Betula pendula", "Youngii"),
    "Euonymous europaeus Red Cascade": ("Euonymus europaeus", "Red Cascade"),
    "Betula albosinesis Fascination": ("Betula albosinensis", "Fascination"),
    "Sorbus aria Magnifica": ("Sorbus aria", "Magnifica"),
    "Sorbus x arnoldiana Schouten": ("Sorbus x arnoldiana", "Schouten"),
    "Amelanchier arborea Robin Hill": ("Amelanchier arborea", "Robin Hill"),
    "Sorbus hybrida Gibbsii": ("Sorbus hybrida", "Gibbsii"),
    "Ilex x altaclarensis Camelliifolia": ("Ilex x altaclerensis", "Camelliifolia"),
    "Fagus sylvatica Asplenifolia": ("Fagus sylvatica", "Asplenifolia"),
    "Acer pseudoplatanus Variegatum": ("Acer pseudoplatanus", "Variegatum"),
    "Acer x freemanii Armstrong": ("Acer x freemanii", "Armstrong"),
    "Carpinus betulus incisa": ("Carpinus betulus", "Incisa"),
    'Acer platinoides "Laciniatum"': ("Acer platanoides", "Laciniatum"),
    # An apostrophe inside the quoted cultivar.
    "Fraxinus excelsior 'Westof's Glory'": ("Fraxinus excelsior", "Westhof's Glory"),
    # 'Paul's Scarlet' is the Midland hawthorn's cultivar; `oxycantha` is no
    # name at all (WCVP), and `oxyacantha` would read as a different taxon.
    "Crataegus oxycantha 'Paul Scarlet'": ("Crataegus laevigata", "Paul's Scarlet"),
}


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def species_of(raw) -> tuple[str, str | None]:
    """`(species string, cultivar)` from `Species`; quoted cultivars are left
    in the string for `enforce_tree_schema` to split."""
    text = clean(raw)
    if text is None:
        return UNKNOWN_SPECIES, None
    return TAXA.get(text, (text, None))


_counts: dict[str, int] = {
    "no TreeCode": 0,
    "felled": 0,
    "not a tree (possible planting site)": 0,
    "unreadable Trunk": 0,
}
_unreadable_labels: dict[str, int] = {}
_rows_read = 0


def trunk_cm(value) -> float | None:
    text = clean(value)
    if text is None:
        return None
    mid = TRUNK_BAND_CM.get(text)
    if mid is None:
        _counts["unreadable Trunk"] += 1
        _unreadable_labels[text] = _unreadable_labels.get(text, 0) + 1
    return mid


def spread_m(value) -> float | None:
    try:
        spread = float(value)
    except (TypeError, ValueError):
        return None
    if not 0 < spread <= MAX_SPREAD_M:
        return None
    return spread


def is_tree(feature: dict) -> bool:
    rec = feature.get("attributes") or {}
    if clean(rec.get("Felled")) == "Yes":
        _counts["felled"] += 1
        return False
    if clean(rec.get("Species")) in NOT_A_TREE:
        _counts["not a tree (possible planting site)"] += 1
        return False
    return True


def iter_row_chunks():
    """One ArcGIS page (2,000 features) at a time, geometry in WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


def transform(features: list[dict]) -> pa.Table:
    global _rows_read
    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []

    for feature in features:
        _rows_read += 1
        rec = feature.get("attributes") or {}
        code = rec.get("TreeCode")
        if code is None:
            _counts["no TreeCode"] += 1
            continue
        lat, lon = esri_point(feature.get("geometry"))
        name, selection = species_of(rec.get("Species"))
        if clean(rec.get("Vitality")) == "Dead":
            name, selection = DEAD_SPECIES, None
        diameter_cm = trunk_cm(rec.get("Trunk"))

        tree_id.append(f"basingstoke-{int(code)}")
        species.append(name)
        cultivar.append(selection)
        plant_date.append(None)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(diameter_cm / 2.54 if diameter_cm else None)
        crown.append(spread_m(rec.get("Spread")))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["GBBSK"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            "crown_width_m": pa.array(crown, type=pa.float64()),
        }
    )


def report() -> None:
    for reason in ("no TreeCode", "felled", "not a tree (possible planting site)"):
        if _counts[reason]:
            print(f"Basingstoke: dropped {_counts[reason]:,} rows: {reason}", file=sys.stderr)
    bad = _counts["unreadable Trunk"]
    if bad:
        print(
            f"Basingstoke: {bad:,} unreadable Trunk values nulled: "
            f"{sorted(_unreadable_labels.items(), key=lambda kv: -kv[1])[:10]}",
            file=sys.stderr,
        )
    if _rows_read and bad / _rows_read > MAX_UNREADABLE_SHARE:
        raise RuntimeError(
            f"Basingstoke: {bad:,} of {_rows_read:,} Trunk values are unreadable; "
            "the bands changed"
        )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, keep=is_tree, label="Basingstoke OpenData")
    report()
    table = validate_coordinates(table, city="Basingstoke", city_code="GBBSK")
    table = enforce_tree_schema(table, city="Basingstoke", data_source="BASINGSTOKE_OPENDATA")
    emit(table)

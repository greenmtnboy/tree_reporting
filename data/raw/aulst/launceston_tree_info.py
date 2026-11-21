#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy", "tzdata"]
# ///
"""Launceston, TAS's council tree inventory.

Source: the City of Launceston's hosted service "ParksAndRecreation" (ArcGIS
item f62d2c83dd94443ab77e933e78de4b4f, owner `lccspatial`, shared to the
council's "Launceston City Council - Open Data" group behind
https://opendata.launceston.tas.gov.au), layer 1 `Trees`: 27,962 rows on
2026-11-21, stored in GDA94 / MGA zone 55 (EPSG:28355) and read reprojected
to WGS84 (`outSR=4326`).  `editingInfo.dataLastEditDate` is 2023-02-22; the
item was last touched in July 2024 (a schema edit).

The council's other tree service, `Trees2` (item 9dcc748454e3495d862d508edf8a8434,
29,273 rows, the layer the council's "Tree Explorer" app draws), is a frozen
February 2019 snapshot with no GlobalID and no `GISAssetID` filled in.  Its
extra columns (`BH_Diameter_cm`, `Crown_Width_m`, `Canopy_Height`) are not a
survey: the diameter is zero on all but 330 rows and the crown width and
height are filled on every row alike.  It is not read.

Licence: the item's own licence field is empty.  The council's copyright
notice on its mapping data (on the Tree Explorer app and its other open-data
items) says: "the City of Launceston grants users of the data a licence ...
to download, print and otherwise reproduce the data, subject to the terms and
conditions of the Creative Commons BY 4.0 licence."  Attribute to the City of
Launceston.

**The id is the layer's `GlobalID`** (`launceston-{guid}`), non-null on every
row; `enforce_tree_schema` refuses a repeat.  `GISAssetID` is empty on 2,083
rows and so is not used.

**Which rows are trees.**  `Mode` is a coded field (active / inactive /
proposed): the 3 `inactive` rows and any `proposed` site are dropped; the 4
rows with no mode are kept.  `Class` (Park / Street / private) is not a
filter: the 963 `private` rows are trees the council surveyed on private land
beside its own, and they are kept.

**Species.**  `GenusSpecies` is a coded field whose domain maps every code to
itself, a scientific name with any cultivar quoted (`Photinia glabra
'Robusta'`); `Eucalyptus species` is the genus only, and `Unknown species`
and a blank value (4,216 rows, blank or null) are unidentified trees, kept.
`NAME_FIXES` are the values the shared reading would get wrong: cultivars
written in the epithet's place (`Olea manzanillo`), crape myrtle hybrids
written as formulas, a poplar clone number, a stray quote, an apostrophe
inside a quoted cultivar.  Misspelt epithets are left to the shared
misspelling map.  The common-name column `Name` is not mapped.

**Plant date.**  `PlantedDate` is set on about 10% of rows.  Esri stamps are
UTC; most are midnight UTC, some local midnight (13:00 or 14:00 UTC), so each
is read as its calendar date in Hobart.  A stamp at the Unix epoch (1969-12-31
or 1970-01-01 in Hobart, 4 rows) is a default, not a planting, and is nulled.

**Not published.**  The layer records no trunk size, height or crown spread,
so `diameter_at_breast_height` is null.  `SurveyDate`, `Accuracy_m`, `Owns`
and `Maintains` are not mapped.
"""

import sys
from datetime import date, datetime
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
    UNKNOWN_CONIFER_SPECIES,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services.arcgis.com/yeXpdyjk3azbqItW/arcgis/rest/services/"
    "ParksAndRecreation/FeatureServer/1",
    timeout=180,
)
FIELDS = "OBJECTID,GlobalID,GenusSpecies,PlantedDate,Mode"
ID_PREFIX = "launceston"
LOCAL_TZ = ZoneInfo("Australia/Hobart")

# `Mode` codes that are not a standing tree.
NOT_STANDING_MODES = frozenset({"inactive", "proposed"})

# Local dates an epoch-zero stamp lands on: a default, not a planting.
PLACEHOLDER_PLANT_DATES = frozenset({date(1969, 12, 31), date(1970, 1, 1)})

# Raw `GenusSpecies` values (whitespace collapsed) mapped to (species, cultivar).
NAME_FIXES: dict[str, tuple[str, str | None]] = {
    # Cultivars written as if they were the epithet.
    "Olea manzanillo": ("Olea europaea", "Manzanillo"),
    "Malus ioensis var plena": ("Malus ioensis", "Plena"),
    "ulmus glabra pendula": ("Ulmus glabra", "Pendula"),
    # A poplar clone number, and the hybrid black poplar written as a formula.
    "Populus 65-1": ("Populus", "65-1"),
    "Populus x P. euramericana 'Veronese'": ("Populus x canadensis", "Veronese"),
    # Crape myrtle hybrids (L. indica x L. fauriei) written as formulas.
    "Lagerstroemia X L.fauriei 'Biloxi'": ("Lagerstroemia", "Biloxi"),
    "Lagerstroemia indica x Lagerstroemia fauriei 'Zuni'": ("Lagerstroemia", "Zuni"),
    "Lagerstroemia x 'Natchez'": ("Lagerstroemia", "Natchez"),
    # The golden weeping willow, Salix x sepulcralis 'Chrysocoma'.
    "Salix x Chysocoma": ("Salix x sepulcralis", "Chrysocoma"),
    # 'Raywood' claret ash, once with the epithet garbled.
    "Fraxinus oxycantha 'Raywoodii'": ("Fraxinus angustifolia", "Raywood"),
    "Fraxinus oxycarpa 'Raywoodii'": ("Fraxinus angustifolia", "Raywood"),
    "Fraxinus angustifolia 'Raywood'": ("Fraxinus angustifolia", "Raywood"),
    # A stray closing quote, and an apostrophe inside a quoted cultivar.
    "Cedrus atlantica 'Glauca Pendula'\"": ("Cedrus atlantica", "Glauca Pendula"),
    "Pyrus communis 'Williiam's Bon Chretien'": ("Pyrus communis", "Williams' Bon Chretien"),
    # A rank written where the epithet goes: a conifer, nothing more.
    "Pinophyta": (UNKNOWN_CONIFER_SPECIES, None),
}


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("\xa0", " ").split())
    return text or None


def species_and_cultivar(raw) -> tuple[str | None, str | None]:
    """`GenusSpecies` as (species, cultivar); a quoted cultivar is left in for
    `enforce_tree_schema` to extract."""
    name = clean(raw)
    if name is None:
        return None, None
    return NAME_FIXES.get(name, (name, None))


def local_plant_date(value) -> date | None:
    stamp: datetime | None = esri_ms_to_datetime(value)
    if stamp is None:
        return None
    day = stamp.astimezone(LOCAL_TZ).date()
    return None if day in PLACEHOLDER_PLANT_DATES else day


def guid(attrs: dict) -> str:
    return str(attrs.get("GlobalID") or "").strip().strip("{}").lower()


class Counts:
    no_id = 0
    no_geometry = 0
    not_standing = 0
    placeholder_dates = 0


COUNTS = Counts()


def keep(feature: dict) -> bool:
    attrs = feature.get("attributes") or {}
    if (clean(attrs.get("Mode")) or "").lower() in NOT_STANDING_MODES:
        COUNTS.not_standing += 1
        return False
    if not guid(attrs):
        COUNTS.no_id += 1
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
    plant_date: list[date | None] = []
    latitude: list[float] = []
    longitude: list[float] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        name, cv = species_and_cultivar(attrs.get("GenusSpecies"))
        planted = local_plant_date(attrs.get("PlantedDate"))
        if planted is None and attrs.get("PlantedDate") is not None:
            COUNTS.placeholder_dates += 1
        tree_id.append(f"{ID_PREFIX}-{guid(attrs)}")
        species.append(name)
        cultivar.append(cv)
        plant_date.append(planted)
        latitude.append(lat)
        longitude.append(lon)

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["AULST"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "cultivar": pa.array(cultivar, type=pa.string()),
        "plant_date": pa.array(plant_date, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array([None] * n, type=pa.float64()),
    })


if __name__ == "__main__":
    table = stream_to_table(iter_chunks(), transform, keep=keep, label="Launceston OpenData")
    print(
        f"Launceston ingest: dropped {COUNTS.not_standing} inactive/proposed rows, "
        f"{COUNTS.no_id} rows with no GlobalID, {COUNTS.no_geometry} rows with no "
        f"geometry; nulled {COUNTS.placeholder_dates} epoch plant dates",
        file=sys.stderr,
    )
    table = validate_coordinates(table, city="Launceston", city_code="AULST")
    table = enforce_tree_schema(
        table, city="Launceston", data_source="LAUNCESTON_OPENDATA"
    )
    emit(table)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Stoughton, WI's public tree inventory.

Source: "Tree Inventory (Public)" (`Tree_Inventory_(Public)/FeatureServer/0`)
on the city's ArcGIS Online organisation, the layer behind the city's "Public
Tree Inventory" Experience Builder app.  7,294 rows when the city was wired
(2026-10-30), of which 6,288 are standing trees.  Paging, the watermark and
Esri's epoch-milliseconds live in `shared.platforms.arcgis`.

**A row is a site, and `TreeStatus` is what stands on it now.**  The layer keeps
a site's history in `TreeRemoved` / `TreeReplaced`, and those columns are
never cleared: 955 rows read `TreeRemoved = 'Removed'` *and* `TreeStatus =
'Tree'` because the old tree came out and a new one went in, with a diameter
of 2-5 inches to match.  So the history columns decide nothing about whether
the row is a tree; `TreeStatus` does.  `Planting Site`, `Empty`, `Stump` and
a null status are dropped (961, 9, 33 and 3 rows); any other value raises,
because a new status is a schema change and guessing which side of the line it
falls on would publish the wrong trees.

**The species is two columns**, `Genus` and `SPECIES` (the epithet), both with
coded-value domains whose codes are the names themselves.  `spp`, blanks and
`N/A` epithets resolve to the genus through the shared hygiene.  The epithet
domain carries its own typos (`acerifolla`, `glyptostoboide`, `meuhlengergil`);
those are folded centrally by `SPECIES_MISSPELLINGS`, not here.

`Variety` is the cultivar column, free text with trailing spaces.  `Common` is
the common name the map's tree card shows.  `DBH` is inches.

**`plant_date` is the replanting date where there is one.**  The layer records
no planting date, but a site whose `TreeReplaced` reads `Replaced` dates the
tree now standing on it with `TreeReplaceDate` (1,284 trees, 2007-2026).
`DATE_` is the inspection date and is not used.

`GlobalID` is the key: 7,294 distinct, no nulls.  `ID_new` is aliased "Tree
ID" but is null on 1,736 rows, and `ID` is aliased "Unused Tree ID".
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_date, esri_point, iter_features
from shared.ingest import (
    emit,
    enforce_tree_schema,
    normalize_species_parts,
    normalize_tree_name,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services7.arcgis.com/iZIPdzAfqdnP9vrA/arcgis/rest/services/"
    "Tree_Inventory_(Public)/FeatureServer/0"
)

OUT_FIELDS = (
    "GlobalID,Genus,SPECIES,Variety,Common,DBH,TreeStatus,TreeReplaced,TreeReplaceDate"
)

TREE_STATUS = "Tree"
NOT_A_TREE_STATUSES = frozenset({"Planting Site", "Empty", "Stump", ""})


def is_a_tree(status: str | None) -> bool:
    """True for a standing tree; raises on a status the ingest has not seen."""
    value = (status or "").strip()
    if value == TREE_STATUS:
        return True
    if value in NOT_A_TREE_STATUSES:
        return False
    raise RuntimeError(
        f"Stoughton TreeStatus {value!r} is new; decide whether it is a tree"
    )


def clean_text(value: str | None) -> str | None:
    """Collapse whitespace; blank and `N/A` are missing."""
    text = " ".join((value or "").split())
    return text if text and text.upper() != "N/A" else None


def plant_date_of(rec: dict) -> date | None:
    """The replanting date, for a site whose tree was replaced."""
    if (rec.get("TreeReplaced") or "").strip() != "Replaced":
        return None
    return esri_ms_to_date(rec.get("TreeReplaceDate"))


def iter_row_chunks():
    return iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    )


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    tree_name: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        if not is_a_tree(rec.get("TreeStatus")):
            continue
        raw_id = str(rec.get("GlobalID") or "").strip()
        tree_id.append(f"sto-{raw_id}" if raw_id else None)
        species.append(
            normalize_species_parts(clean_text(rec.get("Genus")), clean_text(rec.get("SPECIES")))
        )
        cultivar.append(clean_text(rec.get("Variety")))
        tree_name.append(normalize_tree_name(clean_text(rec.get("Common"))))
        plant_date.append(plant_date_of(rec))
        lat, lon = esri_point(feature.get("geometry"))
        latitude.append(lat)
        longitude.append(lon)
        raw_dbh = rec.get("DBH")
        dbh.append(float(raw_dbh) if raw_dbh else None)

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["USSTO"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "tree_name": pa.array(tree_name, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Stoughton OpenData")
    table = validate_coordinates(table, city="Stoughton, WI", city_code="USSTO")
    table = enforce_tree_schema(
        table, city="Stoughton, WI", data_source="STOUGHTON_OPENDATA"
    )
    emit(table)

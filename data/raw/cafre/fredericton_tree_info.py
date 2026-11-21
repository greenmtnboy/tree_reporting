#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Fredericton's street-tree inventory, from the City of Fredericton.

Source: the City's ArcGIS Online organisation (iLWAxhpxafhOza2U), owned by its
Parks & Trees GIS account.  The catalogue entry is "Tree Inventory" (item
1778707bb5a44f72b40aebcb297da092, "Tree inventory of the cities trees in the
street right of way"), shared into the "Fredericton Open Data" group that
builds data-fredericton.opendata.arcgis.com.  The item's own licence field is
empty; the site states that "the City of Fredericton's open data follows the
Open Government License - City of Fredericton ... This license applies to all
information on the City of Fredericton Open Data site"
(https://data-fredericton.opendata.arcgis.com/pages/open-data-license, v1.0,
based on OGL-Canada).  Attribution: "Contains information licensed under the
Open Government License - City of Fredericton."

**This reads the City's fuller view of the same layer**,
`CRM_View_Tree_Inventory_Full` (item 7dad44dc76184da2ab2b5ac6a6c38b4a,
public, built for the City's service-request app), because the catalogue view
exposes only species, common name and street address.  The two are views of
one hosted layer: the same 21,186 rows and the same GlobalIDs (set-equal,
checked 2026-11-21), the same `editingInfo` stamps.  The full view adds the
diameter, the planting year and the removal flag.  Should it ever go away,
`Tree_Inventory/FeatureServer/37` is the catalogue view of the same rows
(no DBH, no planting year, no removal flag).

**The id is `GlobalID`** (`fre-{guid}`): the layer's system GlobalID, non-null
and unique on all 21,186 rows.  `OBJECTID` runs 1..28,725 with gaps -- a layer
edited in place -- but the GlobalID is the publisher's key.

**Species is `Genus_Spec`**, a coded-value domain whose codes are already
Latin names, though rows also carry free-typed values outside it.  Three
quirks are handled here:

  * a *family* typed where the epithet belongs (`Malus Rosaceae`,
    `Acer Sapindaceae`, `Gleditsia Fabaceae`, 263 rows): the tree was
    identified to genus, so the family is dropped;
  * a cultivar typed as the epithet without its quotes (`Quercus Crimschmidt`,
    `Malus Jefspire`, `Malus Hopa`): quoted, so the schema moves it to
    `cultivar` instead of publishing an invented species;
  * `Pyrus.` (a stray full stop).
Capitalised epithets (`Acer Platanoides`) are folded by the schema; the
misspellings (`Acer Platinoides`, `Robina Pseudoacacia`) belong in the shared
misspelling table.  `COMMON_NAM` is never mapped.

**DBH is centimetres** (median 19, 95th percentile 60, maximum 161 over
measured rows; newly planted stock is entered as 5).  `NewDBH` is a later
re-measurement (1,475 rows, mostly the ash-treatment survey) and wins where it
is positive.  Zero is "not measured" and is nulled.

**`PlantingYear`** (2019-2024, 1,367 rows) becomes 1 January of that year.

**Removed trees are dropped.**  `DeleteInve` records the reason a tree was
taken out of the inventory (Site Line, Structure, EAB, Decadent, Construction,
Storm, Home Owner Issue: 251 rows on 2026-11-21); the row stays in the layer
with the reason filled.  `YearRemoved` is empty on every row.  `Detail = Plant`
rows carry a species and a 5 cm diameter: new plantings, kept.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    parse_plant_date_year,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services2.arcgis.com/iLWAxhpxafhOza2U/arcgis/rest/services/"
    "CRM_View_Tree_Inventory_Full/FeatureServer/37",
    timeout=300,
)
OUT_FIELDS = "OBJECTID,GlobalID,Genus_Spec,DBH,NewDBH,PlantingYear,DeleteInve"

# A cultivar typed into the species field as if it were the epithet.
CULTIVAR_AS_EPITHET = {
    "quercus crimschmidt": "Quercus 'Crimschmidt'",
    "malus jefspire": "Malus 'Jefspire'",
    "malus hopa": "Malus 'Hopa'",
}

DROPPED: dict[str, int] = {"removed": 0, "no id": 0}
CLEANED: dict[str, int] = {"family as epithet": 0, "cultivar as epithet": 0}


def scientific_name(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    if text.lower() in CULTIVAR_AS_EPITHET:
        CLEANED["cultivar as epithet"] += 1
        return CULTIVAR_AS_EPITHET[text.lower()]
    tokens = text.split()
    if len(tokens) == 2 and tokens[1].lower().endswith("aceae"):
        CLEANED["family as epithet"] += 1
        return tokens[0]
    if len(tokens) == 1:
        return tokens[0].rstrip(".") or None
    return text


def diameter_inches(rec: dict) -> float | None:
    for field in ("NewDBH", "DBH"):
        value = rec.get(field)
        if value is not None and float(value) > 0:
            return cm_to_inches(float(value))
    return None


def keep(feature: dict) -> bool:
    rec = feature.get("attributes") or {}
    if str(rec.get("DeleteInve") or "").strip():
        DROPPED["removed"] += 1
        return False
    if not str(rec.get("GlobalID") or "").strip():
        DROPPED["no id"] += 1
        return False
    return True


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    plant_date = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        guid = str(rec["GlobalID"]).strip().strip("{}").lower()
        lat, lon = esri_point(feature.get("geometry"))
        tree_id.append(f"fre-{guid}")
        species.append(scientific_name(rec.get("Genus_Spec")))
        plant_date.append(parse_plant_date_year(rec.get("PlantingYear")))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(diameter_inches(rec))

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CAFRE"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


def iter_pages():
    yield from iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    )


if __name__ == "__main__":
    table = stream_to_table(
        iter_pages(), transform, keep=keep, label="Fredericton OpenData"
    )
    print(
        f"Fredericton: dropped {DROPPED['removed']} removed tree(s) and "
        f"{DROPPED['no id']} row(s) without a GlobalID; species cleanup "
        f"{CLEANED}",
        file=sys.stderr,
    )
    # A handful of rows have no geometry (5 on 2026-11-21); they cannot be
    # placed on the map.
    missing = table["latitude"].null_count
    if missing:
        import pyarrow.compute as pc

        table = table.filter(pc.is_valid(table["latitude"]))
        print(f"Fredericton: dropped {missing} row(s) without a geometry", file=sys.stderr)
    table = validate_coordinates(table, city="Fredericton", city_code="CAFRE")
    table = enforce_tree_schema(
        table, city="Fredericton", data_source="FREDERICTON_OPENDATA"
    )
    emit(table)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Arlington, Massachusetts's tree inventory.

The Town's `Public Shade Trees` feature layer (owner `ArlingtonMA_GIS`,
`Public_Shade_Trees`), about 11,400 sites kept by the Tree Warden, Parks,
Cemetery and School Facilities staff.  No licence is stated on the item or
the service: displayed with attribution only, not licensed for reuse.

* **Not every site is a tree.**  `SpaceStatus` marks stumps (`Stump`,
  `Ground Stump`) and empty planting spaces (`Available`, `Not Available`);
  only `Planted` rows are kept, and of those only the ones with no
  `RemovedDate`.
* `Genus` and `Species` are separate coded fields, the epithet capitalised
  (`Acer` / `Platanoides`); `Cultivar` is its own field (`Crimson King`).
* `DBH` is inches, `PlantedDate` the planting date (on about a fifth of
  rows, the plantings since 2016).
* The layer is in Web Mercator; `out_sr` reprojects it.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_date, esri_point, iter_features  # noqa: E402
from shared.ingest import (  # noqa: E402
    emit,
    enforce_tree_schema,
    normalize_species_parts,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services2.arcgis.com/s1Sh73K7qtP9JdrG/arcgis/rest/services/"
    "Public_Shade_Trees/FeatureServer/0"
)
# 36 rows carry a 1970 `PlantedDate` (days after the Unix epoch, an empty
# date that was saved); every real one is from 2016 on.
PLANTED_FROM = 1971
OUT_FIELDS = "OBJECTID,Genus,Species,Cultivar,DBH,PlantedDate,RemovedDate,SpaceStatus"


def is_tree(rec: dict) -> bool:
    return rec.get("SpaceStatus") == "Planted" and rec.get("RemovedDate") is None


def iter_row_chunks():
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


def transform(features: list[dict]) -> pa.Table:
    ids, species, cultivars, planted, lats, lons, dbhs = [], [], [], [], [], [], []
    for feature in features:
        rec = feature.get("attributes") or {}
        if not is_tree(rec):
            continue
        lat, lon = esri_point(feature.get("geometry"))
        ids.append(f"arm-{rec['OBJECTID']}")
        species.append(normalize_species_parts(rec.get("Genus"), rec.get("Species")))
        cultivar = " ".join(str(rec.get("Cultivar") or "").split())
        cultivars.append(cultivar or None)
        plant = esri_ms_to_date(rec.get("PlantedDate"))
        planted.append(plant if plant is None or plant.year >= PLANTED_FROM else None)
        lats.append(lat)
        lons.append(lon)
        dbh = rec.get("DBH")
        dbhs.append(float(dbh) if dbh not in (None, 0) else None)
    return pa.table(
        {
            "tree_id": pa.array(ids, type=pa.string()),
            "city": pa.array(["USARL"] * len(ids), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivars, type=pa.string()),
            "plant_date": pa.array(planted, type=pa.date32()),
            "latitude": pa.array(lats, type=pa.float64()),
            "longitude": pa.array(lons, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbhs, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Arlington, MA tree inventory")
    table = validate_coordinates(table, city="Arlington, MA", city_code="USARL")
    table = enforce_tree_schema(table, city="Arlington, MA", data_source="ARLINGTON_MA")
    emit(table)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""New Orleans, LA's public tree inventory.

Source: the "Trees" layer of the city's 311 asset service,
`OpenGov/OpenGov311_Assets/MapServer/7` on maps.nola.gov: the Parks and
Parkways street-tree asset register (Cartegraph-style condition and risk
columns, a `GlobalID`, an exact DBH and per-row edit stamps).  106,845 rows on
2026-11-11, of which 104,407 are published.  Paging, the watermark and Esri's
epoch-milliseconds live in `shared.platforms.arcgis`.

**Why this layer and not "Tree Location".**  The city's open-data "Tree
Location" (`Basemaps/TreeCanopy/MapServer/0` on gis.nola.gov, 106,820 rows,
mirrored on data.nola.gov as `3tyj-9x33`, and `Basemap/TreeSpecies/
MapServer/1` on maps.nola.gov) is a static export of the same inventory with
four columns (`TREE_ID`, `COMMON`, `BOTANICAL`, the point), no `GlobalID`,
no diameter, no condition and no edit tracking: data.nola.gov's
`rowsUpdatedAt` reads 2025-04-17 and the city warns it may not reflect
current plantings.  The asset layer is the live register that export was
taken from (same species vocabulary, 25 more rows), still edited
(`last_edited_date` reached 2026-09), and it says which sites are stumps or
vacant, which the export cannot.

**`GlobalID` is the key**: 106,845 distinct, no nulls, as is the asset's own
`GISID` (`TR1`..`TR107002`); the publisher-guaranteed id is preferred.

**A row is a site, and `Condition` says what stands on it.**  `Good`, `Fair`,
`Poor`, `Critical` and `Dead` are trees (dead trees stay, by the shared rule);
`Stump` and `Vacant` are dropped (1,766 and 644 rows).  A blank condition (3
rows, live oaks added in 2026 and not yet inspected) is a tree.  Any other
value raises, because a new condition is a schema change and guessing which
side of the line it falls on would publish the wrong trees.  A row with a
`Retired` date (15) is an asset the city has taken out of service and is
dropped whatever its condition reads.  The shared rule then drops the 20
further rows whose species reads `Stump` under a tree condition.

**Species is `ScientificName`**, the binomial, sometimes with a quoted
cultivar (`Ilex x attenuata 'Eagleston'`, moved to `cultivar` by the schema)
or a parenthetical (`Lagerstroemia indica (and hybrids)`, 30,250 rows).  A
handful of rows carry a common name in that column (`Live Oak`, 14; `Japanese
Blueberry Tree`, 1) or a non-taxon (`Unidentifiable Tree`, 15, which would
otherwise publish as the genus "Unidentifiable"); `NON_TAXON_SPECIES` maps
those.  A blank scientific name falls back to `CommonName` through the shared
English table (2 rows).  `CommonName` is the map card's name.

`Diameter` (aliased "Exact DBH") is inches: mean 13, max 100, null on 8.
`Washingtonia filifera X robusta` (3 rows) is the formula hybrid the shared
hygiene truncates to `Washingtonia filifera`, and `Mimosa species` (11 rows,
common name "Mimosa Species") is published as the genus the column names,
though a New Orleans "mimosa" is usually *Albizia julibrissin*; both are left
to the shared rules rather than guessed here.

`Installed` is the only planting date and is set on one row; `Replaced` is
never set.  The coordinates are the layer's State Plane points, reprojected to
WGS84 by the server.
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
    normalize_species,
    normalize_tree_name,
    stream_to_table,
    validate_coordinates,
)
from shared.species.english import species_from_common_name

LAYER = FeatureLayer(
    "https://maps.nola.gov/server/rest/services/OpenGov/OpenGov311_Assets/MapServer/7"
)

OUT_FIELDS = "GlobalID,ScientificName,CommonName,Diameter,Condition,Installed,Retired"

TREE_CONDITIONS = frozenset({"Good", "Fair", "Poor", "Critical", "Dead", ""})
NOT_A_TREE_CONDITIONS = frozenset({"Stump", "Vacant"})

# Values of `ScientificName` that are not a scientific name.  `None`
# publishes `Unknown`.
NON_TAXON_SPECIES: dict[str, str | None] = {
    "Live Oak": "Quercus virginiana",
    "Japanese Blueberry Tree": "Elaeocarpus decipiens",
    "Unidentifiable Tree": None,
}


def is_a_tree(rec: dict) -> bool:
    """True for a standing (or dead) tree; raises on an unseen condition."""
    if rec.get("Retired") is not None:
        return False
    value = (rec.get("Condition") or "").strip()
    if value in TREE_CONDITIONS:
        return True
    if value in NOT_A_TREE_CONDITIONS:
        return False
    raise RuntimeError(
        f"New Orleans tree Condition {value!r} is new; decide whether it is a tree"
    )


def clean_text(value: str | None) -> str | None:
    text = " ".join((value or "").split())
    return text or None


def species_of(rec: dict) -> str | None:
    scientific = clean_text(rec.get("ScientificName"))
    if scientific in NON_TAXON_SPECIES:
        return NON_TAXON_SPECIES[scientific]
    if scientific is None:
        return species_from_common_name(clean_text(rec.get("CommonName")))
    return normalize_species(scientific)


def iter_row_chunks():
    return iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    )


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    tree_name: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        if not is_a_tree(rec):
            continue
        raw_id = str(rec.get("GlobalID") or "").strip().strip("{}")
        tree_id.append(f"nol-{raw_id}" if raw_id else None)
        species.append(species_of(rec))
        tree_name.append(normalize_tree_name(clean_text(rec.get("CommonName"))))
        plant_date.append(esri_ms_to_date(rec.get("Installed")))
        lat, lon = esri_point(feature.get("geometry"))
        latitude.append(lat)
        longitude.append(lon)
        raw_dbh = rec.get("Diameter")
        dbh.append(float(raw_dbh) if raw_dbh else None)

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["USNOL"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "tree_name": pa.array(tree_name, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="New Orleans OpenData")
    table = validate_coordinates(table, city="New Orleans, LA", city_code="USNOL")
    table = enforce_tree_schema(
        table, city="New Orleans, LA", data_source="NEW_ORLEANS_OPENDATA"
    )
    emit(table)

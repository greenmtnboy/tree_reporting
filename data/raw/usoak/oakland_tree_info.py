#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Oakland's public tree inventory, from Oakland Public Works' Park & Tree Services.

No licence stated; displayed with attribution only, not licensed for reuse.
The ArcGIS item (1aeca241016e483cbfac4f2d1974692e) has empty `licenseInfo`
and `accessInformation`, and the service publishes no copyright text.

Source: `Oakland_Public_Tree_Inventory_/FeatureServer/0` in the City of
Oakland's own ArcGIS Online organisation ("City of Oakland GIS", `oakgis`,
org 9tC74aDHuml0x5Yz), owned by the Park & Tree Services account
(`gmatassa_ParkTree`).  The item reads "Public tree inventory, optimized for
mapping, with botanical and common names; version: August 2023", and it is
the layer behind the City's public "Oakland Street & Park Tree Map" (Hub site
oakland-tree-hub-oakgis.hub.arcgis.com, web map e6a75db4176d4aa0beb32e36b1f5a331,
both updated 2025-09).  70,420 rows on 2026-11-20.  Considered and not used:
the 2020 and 2014 inventories in the same org (`Inventory/FeatureServer`,
100,004 rows, last edited 2022-04, superseded by this survey) and the
Socrata "Oakland Street Trees" (data.oaklandca.gov 4jcx-enxf, CC0, 38,613
rows, last updated 2013-01).

**Stumps and snags are recorded in the species field.**  `Species___botanical`
`Stump` (2,123 rows, `Condition` N/A, `Public_Tree` null) are dropped; a
`Snag` (201 rows) is a standing dead tree and publishes as the Dead sentinel.
`Unknown Tree` (117 rows) is used only for dead trees (`Condition` Dead on
every one) and so publishes as Dead too; any other unidentified value with
`Condition` Dead does the same.  `New Planting` (16 young trees, DBH 1-3)
names no taxon and publishes as Unknown, as does `Bamboo species`.

**The id is `Site_ID`**, the inventory's own site number: non-null and unique
over the whole layer (a `groupBy ... having COUNT > 1` statistics query
returns nothing), sparse (9 .. 100,675 over 70,420 rows), whereas `ObjectId`
is a dense 1..70,420, the sign of a layer loaded whole.  The ingest re-checks
uniqueness as it reads and refuses a repeat.  Prefix `oakland-` (Oakville,
CAOAK, uses `oakville-`).

**Species is `Species___botanical`**, a scientific name (`Platanus hybrida`,
`Prunus species`, `Arbutus 'Marina'`); the shared schema folds synonyms and
cuts `species` and cultivars.  `Magnolia grandiflora-little gem` is read as
the species and the cultivar 'Little Gem'.  `Species___common` and the two
`combined` columns are not mapped.

**DBH is `DBH`, whole inches** (median ~10, max 111 on a river red gum).  The
layer records no planting date and no crown spread.  `Latitude`/`Longitude`
duplicate the point geometry; the geometry (requested in WGS84) is read.
"""

import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    DEAD_SPECIES,
    UNKNOWN_SPECIES,
    emit,
    enforce_tree_schema,
    is_not_a_tree,
    normalize_species,
    sanitize_species,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services.arcgis.com/9tC74aDHuml0x5Yz/arcgis/rest/services/"
    "Oakland_Public_Tree_Inventory_/FeatureServer/0",
    timeout=180,
)
OUT_FIELDS = "ObjectId,Site_ID,Species___botanical,DBH,Condition"

ID_PREFIX = "oakland-"

# `Species___botanical` values that are not a taxon as written: (species, cultivar).
SPECIES_OVERRIDES: dict[str, tuple[str, str | None]] = {
    "snag": (DEAD_SPECIES, None),
    "new planting": (UNKNOWN_SPECIES, None),
    "bamboo species": (UNKNOWN_SPECIES, None),
    "magnolia grandiflora-little gem": ("Magnolia grandiflora", "Little Gem"),
}

# Refuse when more than this share of the layer is dropped as stumps: the
# 2023 layer is 3.0% stumps, and a jump means the field changed meaning.
MAX_STUMP_SHARE = 0.15


def species_key(value) -> str:
    return " ".join(str(value or "").split()).lower()


class Oakland:
    def __init__(self) -> None:
        self.dropped: Counter[str] = Counter()
        self.seen = 0
        self.ids: set[int] = set()

    def keep(self, feature: dict) -> bool:
        self.seen += 1
        rec = feature.get("attributes") or {}
        if is_not_a_tree(rec.get("Species___botanical")):
            self.dropped["stump (Species___botanical)"] += 1
            return False
        if rec.get("Site_ID") is None:
            self.dropped["no Site_ID"] += 1
            return False
        return True

    def __call__(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            site_id = int(rec["Site_ID"])
            if site_id in self.ids:
                raise RuntimeError(f"Oakland: Site_ID {site_id} repeats; it is no longer a key")
            self.ids.add(site_id)
            tree_id.append(f"{ID_PREFIX}{site_id}")

            raw = rec.get("Species___botanical")
            key = species_key(raw)
            if key in SPECIES_OVERRIDES:
                sp, cv = SPECIES_OVERRIDES[key]
            else:
                sp, cv = normalize_species(raw), None
            dead = species_key(rec.get("Condition")) == "dead"
            if dead and (sp is None or sp == UNKNOWN_SPECIES or sanitize_species(sp) is None):
                sp = DEAD_SPECIES
            species.append(sp)
            cultivar.append(cv)

            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            raw_dbh = rec.get("DBH")
            dbh.append(float(raw_dbh) if raw_dbh is not None else None)

        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["USOAK"] * n, type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "plant_date": pa.array([None] * n, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            }
        )

    def report(self) -> None:
        for reason, n in sorted(self.dropped.items()):
            print(f"Oakland: dropped {n:,} rows: {reason}", file=sys.stderr)
        stumps = self.dropped["stump (Species___botanical)"]
        if self.seen and stumps / self.seen > MAX_STUMP_SHARE:
            raise RuntimeError(
                f"Oakland: {stumps:,} of {self.seen:,} rows are stumps; "
                "Species___botanical has changed meaning"
            )


def iter_row_chunks():
    """One ArcGIS page (1,000 features) at a time, geometry in WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


if __name__ == "__main__":
    oakland = Oakland()
    table = stream_to_table(
        iter_row_chunks(), oakland, keep=oakland.keep, label="Oakland OpenData"
    )
    oakland.report()
    table = validate_coordinates(table, city="Oakland", city_code="USOAK")
    table = enforce_tree_schema(table, city="Oakland", data_source="OAKLAND_OPENDATA")
    emit(table)

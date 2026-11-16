#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Boulder, CO's public tree inventory, from the city's own ArcGIS server.

Source: "Tree Inventory Open Data" (ArcGIS item
dbbae8bdb0a44d17934243b88e85ef2b, owner `BoulderCO`, licence CC0 1.0), the
layer `parks/TreesOpenData/MapServer/0` on gis.bouldercolorado.gov: "Public
Trees in the Boulder, Colorado Tree Inventory", maintained by the Urban
Forestry Division of Parks and Recreation.  49,588 rows on 2026-11-16, every
one a tree -- street trees (36,811) and park trees (12,777).  The layer is
stored in Colorado State Plane North (EPSG 2876) and carries no lat/lon
columns, so the geometry is read reprojected server-side (`outSR=4326`); all
49,588 have one, inside -105.301..-105.179, 39.965..40.093.

**`GLOBALID` is the key** (`bou-{guid}`, unbraced, lower-case): the
publisher's per-feature id, 49,588 distinct, none null.  `FACILITYID`
(`TREE79142`) is equally unique today and is the asset tag the city's work
orders use, but the runbook prefers the GlobalID and nothing here needs the
tag.

**Species is `LATINNAME`, with `CULTIVAR` beside it.**  The layer has three
naming columns and they disagree, so it matters which:

* `FULLNAME` is the older surveyed name and is `Unknown` on 4,947 rows whose
  other columns name the tree (729 `Ulmus pumila`, 456 `Fraxinus
  pennsylvanica`, ...); it also welds cultivars in unquoted (`Acer negundo
  Sensation`).
* `COMMONNAME` is a coded-value domain (404 English names, code = name), and
  `LATINNAME` / `GENUS` / `CULTIVAR` are kept in step with it: 247 distinct
  binomials, `Unknown` on 39 rows and null on 12.
* `LATINNAME` writes a cultivar of a genus-only taxon into the name itself,
  unquoted -- `Malus Spring Snow` (122), `Malus Radiant`, `Ulmus Triumph`,
  `Ulmus Frontier` -- with the same selection in `CULTIVAR`.  Those would
  read as a genus plus an invented epithet, so the cultivar is cut off the
  end of the name when `CULTIVAR` repeats it (`Malus`, cultivar `Spring
  Snow`).  `spp` is left to `sanitize_species`.

`CULTIVAR` (filled on 2,001 rows) is mapped to `cultivar` as published.

**DBH is `DBHINT`, whole inches**: mode 2, median 7, 0.3% null, a handful
over 80 (the largest 98, a plains cottonwood); `enforce_tree_schema` nulls
anything implausible.  There is no planting date (`LAST_EDITED_DATE` is an
edit stamp), so `plant_date` is null throughout.

No row describes an empty site: `SITETYPE` is the ground the tree stands in
(`Turf`, `Shrub or Mulch Bed`, `Rock Bed`, ...), and no species value is a
vacancy or stump, so nothing is filtered here beyond the shared rule.
`NOTETREE` flags 80 `Champion`, 68 `Notable` and 220 `Memorial` trees; those
are not mapped (a notable register is its own partition, docs/DATA_PIPELINE.md).
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    emit,
    enforce_tree_schema,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://gis.bouldercolorado.gov/ags_svr2/rest/services/parks/TreesOpenData/MapServer/0"
)

OUT_FIELDS = "OBJECTID,GLOBALID,LATINNAME,CULTIVAR,DBHINT"


def tree_id_for(raw) -> str | None:
    """`bou-` plus the GlobalID, unbraced and lower-case; None when missing."""
    text = str(raw or "").strip().strip("{}").strip().lower()
    return f"bou-{text}" if text else None


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def species_for(latin, cultivar: str | None) -> str | None:
    """`LATINNAME` with an unquoted cultivar tail removed.

    `Malus Spring Snow` + `Spring Snow` -> `Malus`.  Only cut when the tail is
    exactly the published `CULTIVAR`, so a real epithet is never touched.
    """
    name = clean(latin)
    if name is None:
        return None
    if cultivar:
        words, tail = name.split(), cultivar.split()
        if len(words) > len(tail) and [w.lower() for w in words[-len(tail):]] == [
            w.lower() for w in tail
        ]:
            name = " ".join(words[: -len(tail)])
    return normalize_species(name)


def iter_row_chunks():
    """One ArcGIS page (2,000 features) at a time, geometry in WGS84."""
    return iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    )


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        tid = tree_id_for(rec.get("GLOBALID"))
        if tid is None:
            print(
                f"Boulder: skipping OBJECTID {rec.get('OBJECTID')} with no GLOBALID",
                file=sys.stderr,
            )
            continue
        lat, lon = esri_point(feature.get("geometry"))
        cv = clean(rec.get("CULTIVAR"))

        tree_id.append(tid)
        species.append(species_for(rec.get("LATINNAME"), cv))
        cultivar.append(cv)
        plant_date.append(None)
        latitude.append(lat)
        longitude.append(lon)
        raw_dbh = rec.get("DBHINT")
        dbh.append(float(raw_dbh) if raw_dbh is not None else None)

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["USBOU"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Boulder OpenData")
    table = validate_coordinates(table, city="Boulder", city_code="USBOU")
    table = enforce_tree_schema(table, city="Boulder", data_source="BOULDER_OPENDATA")
    emit(table)

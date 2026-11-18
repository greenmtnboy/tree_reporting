#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Bern's landmarks: the city's sights and the Bauinventar's building ensembles.

Both are layers on the same geoportal ArcGIS server as the trees
(`map.bern.ch`, Geoinformation Stadt Bern), read live, and both are on
opendata.swiss under `terms_open` ("Open use. Use for commercial purposes
allowed."):

* **Sehenswürdigkeiten** (`Geoportal/Sehenswuerdigkeiten/MapServer/0`,
  opendata.swiss `sehenswurdigkeiten`): the city's own list of sights -- the
  Münster, the Zytglogge, the Bundeshaus, the Käfigturm, the BärenPark, the
  Rosengarten -- 20 points, of which 19 are in the municipality (`BFS` 351);
  the Gurten (Wabern, `BFS` 355) is left out.  Keyed `bern-sw-{Objectid}`,
  with the street address.
* **Bauinventar, Baugruppen** (`Geoportal/Bauinventar/MapServer/2`,
  opendata.swiss `bauinventar1`): the city monument office's inventory of
  building ensembles, 74 named `Baugruppe` polygons (`Schloss Holligen`,
  `Tscharnergut`, `Obere Altstadt`, `Landsitz Schönberg`).  The layer's 35
  `Strukturgruppe` areas -- settlement-pattern zones such as `Weissenbühl
  Ost` -- are not landmarks and are left out.  Keyed `bern-bg-{Objectid}`.

**The Bauinventar's 10,357 individual buildings (`.../Bauinventar/MapServer/1`)
are not published**: the layer carries no name, only a rating
(`schützenswert`, `erhaltenswert`, `beachtenswert`) and a link to a PDF
datasheet named after the address.  The Kanton Bern's Bauinventar download
(opendata.swiss `bauinventar`, the same `terms_open`) holds 6,810 objects in
the city with the same gap: a street address and a rating, no name.  The old
town is not itemised in either: the city's register says every building in
it is protected.

`category` names the source (`Sehenswürdigkeit`, `Baugruppe`).
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features

SIGHTS = FeatureLayer(
    "https://map.bern.ch/arcgis/rest/services/Geoportal/Sehenswuerdigkeiten/MapServer/0"
)
GROUPS = FeatureLayer(
    "https://map.bern.ch/arcgis/rest/services/Geoportal/Bauinventar/MapServer/2"
)
BERN_BFS = 351  # the Federal Statistical Office's number for the municipality

COLUMNS = ("landmark_id", "name", "geometry_raw", "category", "address")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("_", " ").split())
    return text if text and text != "<Null>" else None


def read_sights(rows: dict[str, list]) -> int:
    kept = outside = 0
    for page in iter_features(
        SIGHTS,
        out_fields="Objectid,Punktname,Strasse,Hausnummer,BFS",
        return_geometry=True,
        out_sr=4326,
        order_by="Objectid",
    ):
        for feature in page:
            attrs = feature.get("attributes") or {}
            name = clean(attrs.get("Punktname"))
            wkt = esri_geometry_to_wkt(feature.get("geometry"))
            if name is None or wkt is None or attrs.get("Objectid") is None:
                continue
            if attrs.get("BFS") != BERN_BFS:
                outside += 1
                continue
            street, number = clean(attrs.get("Strasse")), clean(attrs.get("Hausnummer"))
            rows["landmark_id"].append(f"bern-sw-{int(attrs['Objectid'])}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["category"].append("Sehenswürdigkeit")
            rows["address"].append(" ".join(p for p in (street, number) if p) or None)
            kept += 1
    if not kept:
        raise RuntimeError(f"no sights read from {SIGHTS.base}")
    print(f"Bern Sehenswürdigkeiten: {kept} kept, {outside} outside the municipality", file=sys.stderr)
    return kept


def read_groups(rows: dict[str, list]) -> int:
    kept = structure = 0
    for page in iter_features(
        GROUPS,
        out_fields="Objectid,Charakter_beschrieb,Name",
        return_geometry=True,
        out_sr=4326,
        order_by="Objectid",
    ):
        for feature in page:
            attrs = feature.get("attributes") or {}
            name = clean(attrs.get("Name"))
            wkt = esri_geometry_to_wkt(feature.get("geometry"))
            if name is None or wkt is None or attrs.get("Objectid") is None:
                continue
            if clean(attrs.get("Charakter_beschrieb")) != "Baugruppe":
                structure += 1
                continue
            rows["landmark_id"].append(f"bern-bg-{int(attrs['Objectid'])}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["category"].append("Baugruppe")
            rows["address"].append(None)
            kept += 1
    if not kept:
        raise RuntimeError(f"no Baugruppen read from {GROUPS.base}")
    print(
        f"Bern Bauinventar: {kept} Baugruppen kept, {structure} Strukturgruppen left out",
        file=sys.stderr,
    )
    return kept


def main() -> None:
    rows: dict[str, list] = {c: [] for c in COLUMNS}
    read_sights(rows)
    read_groups(rows)
    n = len(rows["landmark_id"])
    emit(
        pa.table(
            {
                "landmark_id": pa.array(rows["landmark_id"], type=pa.string()),
                "city": pa.array(["CHBRN"] * n, type=pa.string()),
                "name": pa.array(rows["name"], type=pa.string()),
                "geometry_raw": pa.array(rows["geometry_raw"], type=pa.string()),
                "category": pa.array(rows["category"], type=pa.string()),
                "address": pa.array(rows["address"], type=pa.string()),
            }
        )
    )


if __name__ == "__main__":
    main()

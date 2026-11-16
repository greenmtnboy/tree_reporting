#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Vienna's landmarks: its historic gardens and its sights, from the city's WFS.

Both are layers on `data.wien.gv.at/daten/geo`, the GeoServer the trees come
from, and both are Stadt Wien open data under CC BY 4.0 ("Datenquelle: Stadt
Wien - https://data.wien.gv.at"):

* `ogdwien:HISTGARTENOGD`, "Historische Gartenanlagen in Wien" (data.gv.at
  fbfc06e1-856f-415a-b61f-a5e164f87355) -- the city's inventory of historic
  gardens and parks, 411 polygons: palace and villa gardens, Schlossparks,
  the Gemeindebau courtyards, city parks with a history.  It is the closest
  thing on the city's portal to a designation register, and it is about
  green space, which is what a tree sits in.  Category `Historic garden`.
* `ogdwien:SEHENSWUERDIGOGD`, "Sehenswürdigkeiten Standorte Wien"
  (f4e80988-c139-4953-8176-b3d6d03f6449) -- 63 points, the sights the city
  itself lists (Stephansdom, Hofburg, Riesenrad, Karlskirche).  The gardens
  register names Schönbrunn's park but not the Opera or the cathedral, and
  these are the places people call a neighbourhood by.  Category `Sight`.

The garden register names some entries by building type alone (`Gemeindebau`
21 times, `Villa`, `Wohnhaus`, `Pfarrhof`); a name that repeats in the layer
gets its first address appended (`Gemeindebau, Berzeliusgasse 9-13`) so the
label says which one.  Keyed on each layer's `OBJECTID` -- unique per layer,
small and stable, unlike the tree layer's reload sequence -- with the layer in
the prefix.  A row with no name or no geometry is dropped with a count.
"""

import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit
from shared.platforms.wfs import WfsLayer, geojson_to_wkt, iter_wfs_features

BASE_URL = "https://data.wien.gv.at/daten/geo"
GARDENS = WfsLayer(BASE_URL, "ogdwien:HISTGARTENOGD")
SIGHTS = WfsLayer(BASE_URL, "ogdwien:SEHENSWUERDIGOGD")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def read_layer(layer: WfsLayer, properties: list[str]) -> list[dict]:
    features: list[dict] = []
    for page in iter_wfs_features(layer, sort_by="OBJECTID", properties=properties):
        features.extend(page)
    return features


def read_gardens(unusable: Counter) -> list[dict]:
    features = read_layer(GARDENS, ["OBJECTID", "BEZEICHNUNG", "ADRESSE_1", "SHAPE"])
    names = Counter(clean((f.get("properties") or {}).get("BEZEICHNUNG")) for f in features)
    records: list[dict] = []
    for feature in features:
        props = feature.get("properties") or {}
        oid = props.get("OBJECTID")
        name = clean(props.get("BEZEICHNUNG"))
        wkt = geojson_to_wkt(feature.get("geometry"))
        if oid is None or name is None or wkt is None:
            unusable["garden"] += 1
            continue
        address = clean(props.get("ADRESSE_1"))
        if names[name] > 1 and address:
            name = f"{name}, {address}"
        records.append(
            {
                "landmark_id": f"vie-garden-{oid}",
                "name": name,
                "geometry_raw": wkt,
                "category": "Historic garden",
            }
        )
    return records


def read_sights(unusable: Counter) -> list[dict]:
    records: list[dict] = []
    for feature in read_layer(SIGHTS, ["OBJECTID", "NAME", "SHAPE"]):
        props = feature.get("properties") or {}
        oid = props.get("OBJECTID")
        name = clean(props.get("NAME"))
        wkt = geojson_to_wkt(feature.get("geometry"))
        if oid is None or name is None or wkt is None:
            unusable["sight"] += 1
            continue
        records.append(
            {
                "landmark_id": f"vie-sight-{oid}",
                "name": name,
                "geometry_raw": wkt,
                "category": "Sight",
            }
        )
    return records


def read_landmarks() -> list[dict]:
    unusable: Counter = Counter()
    gardens = read_gardens(unusable)
    sights = read_sights(unusable)
    print(
        f"Vienna landmarks: {len(gardens)} historic gardens and {len(sights)} sights "
        f"kept; dropped {unusable['garden']} garden(s) and {unusable['sight']} "
        "sight(s) with no name, geometry or id",
        file=sys.stderr,
    )
    return gardens + sights


def transform(records: list[dict]) -> pa.Table:
    return pa.table(
        {
            "landmark_id": pa.array([r["landmark_id"] for r in records], type=pa.string()),
            "city": pa.array(["ATVIE"] * len(records), type=pa.string()),
            "name": pa.array([r["name"] for r in records], type=pa.string()),
            "geometry_raw": pa.array([r["geometry_raw"] for r in records], type=pa.string()),
            "category": pa.array([r["category"] for r in records], type=pa.string()),
        }
    )


if __name__ == "__main__":
    emit(transform(read_landmarks()))

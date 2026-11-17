#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Frankfurt's designated natural monuments, from the city's WFS.

Two registers of the Untere Naturschutzbehörde (the Umweltamt), both published
by the Stadt Frankfurt am Main on geowebdienste.frankfurt.de (ArcGIS Server
WFS) under the Datenlizenz Deutschland -- Namensnennung -- 2.0, as catalogued
in the city's geodata catalogue (geodatenkatalog.frankfurt.de, records
e0ffbd65-8993-43cb-9d95-43595b8acb34 and 458ae8e0-41bc-406e-bcc2-f60c5f607776)
and harvested to GovData:

* `Naturdenkmale:Naturdenkmale` -- natural monuments designated under
  § 28 BNatSchG, 38 of them in 44 polygons: mostly single named trees
  (`Eschersheimer Linde`, `Urweltmammutbaum im Botanischen Garten`,
  `Friedenseiche`), a few groves, ponds and terraces;
* `Geschuetzte_Landschaftsbestandteile:...` -- protected landscape features
  under § 29 BNatSchG, 6 springs in 11 polygons.

The landmark is the designation, keyed on its `Natureg_ID` -- Hessen's
NATUREG register number (`3412001`), which a designation keeps however it is
redrawn: the five swamp cypresses of `Sumpfzypressen im Zoologischen Garten`
are five polygons of one designation, published as one MULTIPOLYGON.  `GmlID`
and `OBJECTID` are row numbers of the layer.

Frankfurt's built heritage (the Hessian Denkmalliste, kept by the
Landesamt für Denkmalpflege) is published only as a map (WMS, DenkXweb) and
so is not read; neither is the Grünflächenamt's "Frankfurter Baumliste",
also map-only.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import CITY_BOUNDS, emit, get_json_with_retry
from shared.platforms.wfs import geojson_to_wkt

TIMEOUT = 120
# (service URL, feature type, id slug, category)
LAYERS = (
    (
        "https://geowebdienste.frankfurt.de/WFS_Naturdenkmale",
        "Naturdenkmale:Naturdenkmale",
        "nd",
        "Natural monument",
    ),
    (
        "https://geowebdienste.frankfurt.de/WFS_Geschuetzte_Landschaftsbestandteile",
        "Geschuetzte_Landschaftsbestandteile:Geschuetzte_Landschaftsbestandteile",
        "glb",
        "Protected landscape feature",
    ),
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def read_layer(url: str, type_name: str) -> list[dict]:
    payload = get_json_with_retry(
        url,
        params={
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": type_name,
            "SRSNAME": "EPSG:4326",
            "outputFormat": "GEOJSON",
        },
        timeout=TIMEOUT,
    )
    features = payload.get("features") if isinstance(payload, dict) else None
    if not features:
        raise RuntimeError(f"Frankfurt WFS: {type_name} returned no features")
    return features


def polygons(geometry: dict) -> list:
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "Polygon":
        return [coords]
    if kind == "MultiPolygon":
        return list(coords)
    return []


def _first_point(parts: list) -> tuple[float, float] | None:
    coords = parts
    while isinstance(coords, list) and coords and isinstance(coords[0], list):
        coords = coords[0]
    if isinstance(coords, list) and len(coords) >= 2:
        return float(coords[0]), float(coords[1])
    return None


def read_landmarks() -> list[dict]:
    south, north, west, east = CITY_BOUNDS["DEFRA"]
    records: list[dict] = []
    for url, type_name, slug, category in LAYERS:
        groups: dict[int, dict] = {}
        dropped = {"unusable": 0, "unnamed": 0, "outside": 0}
        features = read_layer(url, type_name)
        for feature in features:
            props = feature.get("properties") or {}
            register_id = props.get("Natureg_ID")
            name = clean(props.get("Name"))
            parts = polygons(feature.get("geometry") or {})
            point = _first_point(parts)
            if register_id in (None, "") or not parts or point is None:
                dropped["unusable"] += 1
                continue
            if name is None:
                dropped["unnamed"] += 1
                continue
            lon, lat = point
            if not (south <= lat <= north and west <= lon <= east):
                dropped["outside"] += 1
                continue
            group = groups.setdefault(int(register_id), {"names": set(), "parts": []})
            group["names"].add(name)
            group["parts"].extend(parts)
        for register_id in sorted(groups):
            group = groups[register_id]
            if len(group["names"]) != 1:
                raise RuntimeError(
                    f"Frankfurt {type_name}: Natureg_ID {register_id} carries "
                    f"several names {sorted(group['names'])}; the id no longer "
                    "identifies one designation"
                )
            records.append(
                {
                    "landmark_id": f"frankfurt-{slug}-{register_id}",
                    "name": next(iter(group["names"])),
                    "geometry_raw": geojson_to_wkt(
                        {"type": "MultiPolygon", "coordinates": group["parts"]}
                    ),
                    "category": category,
                }
            )
        print(
            f"Frankfurt {type_name}: read {len(features)} feature(s), kept "
            f"{len(groups)} designation(s), dropped {dropped}",
            file=sys.stderr,
        )
    return records


def transform(records: list[dict]) -> pa.Table:
    return pa.table(
        {
            "landmark_id": pa.array([r["landmark_id"] for r in records], type=pa.string()),
            "city": pa.array(["DEFRA"] * len(records), type=pa.string()),
            "name": pa.array([r["name"] for r in records], type=pa.string()),
            "geometry_raw": pa.array([r["geometry_raw"] for r in records], type=pa.string()),
            "category": pa.array([r["category"] for r in records], type=pa.string()),
        }
    )


if __name__ == "__main__":
    emit(transform(read_landmarks()))

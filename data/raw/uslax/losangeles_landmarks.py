#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit

QUERY_URL = (
    "https://services5.arcgis.com/7nsPwEMP38bSkCjy/ArcGIS/rest/services/"
    "Historic_Cultural_Monuments/FeatureServer/4/query"
)
PAGE_SIZE = 2000


def _close_ring(ring: list[list[float]]) -> list[list[float]]:
    if not ring:
        return ring
    if ring[0] != ring[-1]:
        return [*ring, ring[0]]
    return ring


def _ring_to_wkt(ring: list[list[float]]) -> str:
    closed = _close_ring([[float(x), float(y)] for x, y in ring])
    return "(" + ", ".join(f"{x} {y}" for x, y in closed) + ")"


def geometry_to_polygons(geometry: dict | None) -> list[list[list[float]]]:
    if not geometry:
        return []
    geo_type = geometry.get("type")
    coords = geometry.get("coordinates")
    if not coords:
        return []

    if geo_type == "Polygon":
        return [coords]

    if geo_type == "MultiPolygon":
        return [polygon for polygon in coords if polygon]

    return []


def polygons_to_wkt(polygons: list[list[list[float]]]) -> str | None:
    rendered = []
    for polygon in polygons:
        rings = [_ring_to_wkt(ring) for ring in polygon if ring]
        if rings:
            rendered.append("(" + ", ".join(rings) + ")")

    if not rendered:
        return None
    if len(rendered) == 1:
        return f"POLYGON{rendered[0]}"
    return f"MULTIPOLYGON({', '.join(rendered)})"


def fetch_all_features() -> list[dict]:
    import requests

    offset = 0
    features: list[dict] = []
    session = requests.Session()

    while True:
        params = {
            "where": "1=1",
            "outFields": "OBJECTID,MNT_NUM,NAME,LOCATION,DATE_ACTIVE",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
            "resultRecordCount": str(PAGE_SIZE),
            "resultOffset": str(offset),
        }
        response = session.get(QUERY_URL, params=params, timeout=240)
        response.raise_for_status()
        batch = response.json().get("features", [])
        if not batch:
            break
        features.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return features


def transform(features: list[dict]) -> pa.Table:
    grouped: dict[str, dict[str, object]] = {}

    for feature in features:
        props = feature.get("properties", {})
        geometry = feature.get("geometry")

        raw_id = props.get("MNT_NUM") or props.get("OBJECTID")
        if raw_id is None:
            continue
        stable_id = str(raw_id).strip()
        if not stable_id:
            continue

        raw_name = (
            (props.get("NAME") or "").strip()
            or (props.get("LOCATION") or "").strip()
            or stable_id
        )
        if not raw_name:
            continue

        polygons = geometry_to_polygons(geometry)
        if not polygons:
            continue

        if stable_id not in grouped:
            grouped[stable_id] = {
                "name": raw_name,
                "polygons": [],
            }

        entry = grouped[stable_id]
        if not entry["name"] and raw_name:
            entry["name"] = raw_name
        entry["polygons"].extend(polygons)

    landmark_ids: list[str] = []
    names: list[str] = []
    cities: list[str] = []
    geometry_raws: list[str | None] = []

    for stable_id, entry in grouped.items():
        geometry_raw = polygons_to_wkt(entry["polygons"])
        if geometry_raw is None:
            continue

        landmark_ids.append(f"lax-hcm-{stable_id}")
        names.append(entry["name"])
        cities.append("USLAX")
        geometry_raws.append(geometry_raw)

    return pa.table(
        {
            "landmark_id": pa.array(landmark_ids, type=pa.string()),
            "name": pa.array(names, type=pa.string()),
            "city": pa.array(cities, type=pa.string()),
            "geometry_raw": pa.array(geometry_raws, type=pa.string()),
        }
    )


if __name__ == "__main__":
    emit(transform(fetch_all_features()))

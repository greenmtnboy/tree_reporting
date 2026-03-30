#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests"]
# ///

import json
import sys
from datetime import datetime, timezone

import pyarrow as pa
import requests

from _ecoregion_shared import (
    LAYER_METADATA_URL,
    LAYER_QUERY_URL,
    SERVICE_ITEM_ID,
    SOURCE_VERSION,
)

PAGE_SIZE = 50
GEOMETRY_PRECISION = 5
MAX_ALLOWABLE_OFFSET = 0.05

REALM_MAP = {
    "Nearctic": "nearctic",
    "Palearctic": "palearctic",
    "Neotropic": "neotropical",
    "Afrotropic": "afrotropical",
    "Indomalayan": "indo_malay",
    "Australasia": "australasia",
    "Oceania": "oceania",
    "Antarctica": "antarctic",
}
BASE_WHERE = "ECO_ID IS NOT NULL AND ECO_ID > 0"


def fetch_layer_metadata() -> dict:
    response = requests.get(LAYER_METADATA_URL, timeout=60)
    response.raise_for_status()
    return response.json()


def fetch_feature_count() -> int:
    response = requests.get(
        LAYER_QUERY_URL,
        params={
            "where": BASE_WHERE,
            "returnCountOnly": "true",
            "f": "json",
        },
        timeout=60,
    )
    response.raise_for_status()
    return int(response.json()["count"])


def fetch_feature_page(offset: int, page_size: int) -> list[dict]:
    response = requests.get(
        LAYER_QUERY_URL,
        params={
            "where": BASE_WHERE,
            "outFields": "*",
            "returnGeometry": "true",
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": page_size,
            "orderByFields": "ECO_ID",
            "outSR": 4326,
            "geometryPrecision": GEOMETRY_PRECISION,
            "maxAllowableOffset": MAX_ALLOWABLE_OFFSET,
        },
        timeout=180,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("features", [])


def ring_signed_area(ring: list[list[float]]) -> float:
    area = 0.0
    for idx, (x1, y1) in enumerate(ring):
        x2, y2 = ring[(idx + 1) % len(ring)]
        area += x1 * y2 - x2 * y1
    return area / 2.0


def close_ring(ring: list[list[float]]) -> list[list[float]]:
    if not ring:
        return ring
    if ring[0] == ring[-1]:
        return ring
    return [*ring, ring[0]]


def point_in_ring(point: list[float], ring: list[list[float]]) -> bool:
    x, y = point
    inside = False
    closed = close_ring(ring)
    for idx in range(len(closed) - 1):
        x1, y1 = closed[idx]
        x2, y2 = closed[idx + 1]
        intersects = ((y1 > y) != (y2 > y)) and (
            x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1
        )
        if intersects:
            inside = not inside
    return inside


def arcgis_polygon_to_geojson_geometry(geometry: dict | None) -> dict | None:
    if not geometry:
        return None
    rings = geometry.get("rings") or []
    if not rings:
        return None

    normalized_rings = [close_ring([[float(x), float(y)] for x, y in ring]) for ring in rings if ring]
    outers: list[list[list[float]]] = []
    holes: list[list[list[float]]] = []
    for ring in normalized_rings:
        if ring_signed_area(ring) < 0:
            outers.append(ring)
        else:
            holes.append(ring)

    if not outers:
        outers = normalized_rings
        holes = []

    polygons: list[list[list[list[float]]]] = [[outer] for outer in outers]
    for hole in holes:
        hole_seed = hole[0]
        for polygon in polygons:
            if point_in_ring(hole_seed, polygon[0]):
                polygon.append(hole)
                break

    if len(polygons) == 1:
        return {
            "type": "Polygon",
            "coordinates": polygons[0],
        }
    return {
        "type": "MultiPolygon",
        "coordinates": polygons,
    }


def iter_coords(geometry: dict | None):
    if not geometry:
        return
    if geometry["type"] == "Polygon":
        for ring in geometry["coordinates"]:
            for coord in ring:
                yield coord
    elif geometry["type"] == "MultiPolygon":
        for polygon in geometry["coordinates"]:
            for ring in polygon:
                for coord in ring:
                    yield coord


def polygon_centroid(ring: list[list[float]]) -> tuple[float, float, float]:
    area_acc = 0.0
    cx_acc = 0.0
    cy_acc = 0.0
    closed = close_ring(ring)
    for idx in range(len(closed) - 1):
        x1, y1 = closed[idx]
        x2, y2 = closed[idx + 1]
        cross = x1 * y2 - x2 * y1
        area_acc += cross
        cx_acc += (x1 + x2) * cross
        cy_acc += (y1 + y2) * cross
    area = area_acc / 2.0
    if abs(area) < 1e-12:
        return 0.0, closed[0][0], closed[0][1]
    return area, cx_acc / (6.0 * area), cy_acc / (6.0 * area)


def compute_centroid_and_bbox(geometry: dict | None) -> tuple[float | None, float | None, float | None, float | None, float | None, float | None]:
    coords = list(iter_coords(geometry))
    if not coords:
        return None, None, None, None, None, None

    xs = [coord[0] for coord in coords]
    ys = [coord[1] for coord in coords]
    bbox_min_lon = min(xs)
    bbox_max_lon = max(xs)
    bbox_min_lat = min(ys)
    bbox_max_lat = max(ys)

    weighted_area = 0.0
    weighted_cx = 0.0
    weighted_cy = 0.0

    if geometry["type"] == "Polygon":
        polygon_groups = [geometry["coordinates"]]
    else:
        polygon_groups = geometry["coordinates"]

    for polygon in polygon_groups:
        outer_area, outer_cx, outer_cy = polygon_centroid(polygon[0])
        weight = abs(outer_area)
        weighted_area += weight
        weighted_cx += outer_cx * weight
        weighted_cy += outer_cy * weight

    if weighted_area > 0:
        centroid_lon = weighted_cx / weighted_area
        centroid_lat = weighted_cy / weighted_area
    else:
        centroid_lon = (bbox_min_lon + bbox_max_lon) / 2.0
        centroid_lat = (bbox_min_lat + bbox_max_lat) / 2.0

    return (
        centroid_lat,
        centroid_lon,
        bbox_min_lat,
        bbox_min_lon,
        bbox_max_lat,
        bbox_max_lon,
    )


def derive_source_version(metadata: dict) -> str:
    editing_info = metadata.get("editingInfo", {})
    data_last_edit = editing_info.get("dataLastEditDate")
    if data_last_edit is None:
        return SOURCE_VERSION
    last_edit_iso = datetime.fromtimestamp(
        data_last_edit / 1000,
        tz=timezone.utc,
    ).date().isoformat()
    return f"{SOURCE_VERSION}:{SERVICE_ITEM_ID}:{last_edit_iso}"


def to_row(feature: dict, source_version: str, enriched_at: datetime) -> dict:
    attrs = feature.get("attributes", {})
    geometry = arcgis_polygon_to_geojson_geometry(feature.get("geometry"))
    (
        centroid_lat,
        centroid_lon,
        bbox_min_lat,
        bbox_min_lon,
        bbox_max_lat,
        bbox_max_lon,
    ) = compute_centroid_and_bbox(geometry)

    eco_biome = attrs.get("ECO_BIOME_")
    realm_code = eco_biome[:2] if isinstance(eco_biome, str) and len(eco_biome) >= 2 else None
    raw_realm = attrs.get("REALM")

    return {
        "ecoregion_id": int(attrs["ECO_ID"]),
        "ecoregion_name": attrs.get("ECO_NAME"),
        "realm": REALM_MAP.get(raw_realm, raw_realm.lower().replace(" ", "_") if isinstance(raw_realm, str) else None),
        "biome": attrs.get("BIOME_NAME"),
        "biome_code": int(attrs["BIOME_NUM"]) if attrs.get("BIOME_NUM") is not None else None,
        "realm_code": realm_code,
        "country_codes": None,
        "centroid_lat": centroid_lat,
        "centroid_lon": centroid_lon,
        "geometry_geojson": json.dumps(geometry, separators=(",", ":")) if geometry else None,
        "bbox_min_lat": bbox_min_lat,
        "bbox_min_lon": bbox_min_lon,
        "bbox_max_lat": bbox_max_lat,
        "bbox_max_lon": bbox_max_lon,
        "is_terrestrial": True,
        "source_version": source_version,
        "enriched_at": enriched_at,
    }


SCHEMA = pa.schema(
    [
        ("ecoregion_id", pa.int32()),
        ("ecoregion_name", pa.string()),
        ("realm", pa.string()),
        ("biome", pa.string()),
        ("biome_code", pa.int32()),
        ("realm_code", pa.string()),
        ("country_codes", pa.list_(pa.string())),
        ("centroid_lat", pa.float64()),
        ("centroid_lon", pa.float64()),
        ("geometry_geojson", pa.string()),
        ("bbox_min_lat", pa.float64()),
        ("bbox_min_lon", pa.float64()),
        ("bbox_max_lat", pa.float64()),
        ("bbox_max_lon", pa.float64()),
        ("is_terrestrial", pa.bool_()),
        ("source_version", pa.string()),
        ("enriched_at", pa.timestamp("us", tz="UTC")),
    ]
)


def emit(rows: list[dict]) -> None:
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == "__main__":
    metadata = fetch_layer_metadata()
    total = fetch_feature_count()
    source_version = derive_source_version(metadata)
    enriched_at = datetime.now(tz=timezone.utc)

    rows: list[dict] = []
    for offset in range(0, total, PAGE_SIZE):
        features = fetch_feature_page(offset, PAGE_SIZE)
        rows.extend(to_row(feature, source_version, enriched_at) for feature in features)

    emit(rows)

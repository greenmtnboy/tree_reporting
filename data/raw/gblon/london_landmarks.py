#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

"""
Fetch London listed buildings from Overpass API (OpenStreetMap) and emit Arrow IPC to stdout.

Source: OpenStreetMap via Overpass API
Tag filter: heritage:operator="Historic England" within Greater London bounding box
~870 records with stable ref:GB:nhle IDs from the National Heritage List for England.

Field mapping:
  ref:GB:nhle (or OSM element id fallback) -> landmark_id (prefixed "gblon-")
  name                                     -> name
  lat/lon or center lat/lon                -> latitude, longitude, geometry_raw
"""

import os
import sys
import pyarrow as pa
from pathlib import Path
from typing import Any
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    emit,
    validate_coordinates,
    post_json_with_retry,
    make_point_wkt,
    OVERPASS_HEADERS,
)

# Default is the main instance; point OVERPASS_URL at a mirror (e.g.
# https://overpass.kumi.systems/api/interpreter) when it is overloaded.
OVERPASS_URL = os.environ.get(
    "OVERPASS_URL", "https://overpass-api.de/api/interpreter"
)

# Greater London bounding box (south, west, north, east)
BBOX = "51.28,-0.56,51.70,0.35"

QUERY = f"""
[out:json][timeout:120];
(
  node["heritage:operator"="Historic England"]({BBOX});
  way["heritage:operator"="Historic England"]({BBOX});
  relation["heritage:operator"="Historic England"]({BBOX});
);
out center;
"""


def fetch_elements() -> list[dict]:
    payload = post_json_with_retry(OVERPASS_URL, data={"data": QUERY}, timeout=180, headers=OVERPASS_HEADERS)
    return payload["elements"]


def _landmark_id(element: dict[str, Any]) -> str:
    tags = element.get("tags", {})
    nhle = tags.get("ref:GB:nhle")
    return f"gblon-{nhle}" if nhle else f"gblon-osm-{element['id']}"


def _element_score(element: dict[str, Any]) -> tuple[int, int, int, int, int]:
    tags = element.get("tags", {})
    return (
        1 if tags.get("wikidata") else 0,
        1 if tags.get("addr:housenumber") else 0,
        1 if tags.get("addr:street") else 0,
        len(tags.get("name") or ""),
        -int(element["id"]),
    )


def dedupe_elements(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for element in elements:
        tags = element.get("tags", {})
        if not tags.get("name"):
            continue
        lid = _landmark_id(element)
        existing = deduped.get(lid)
        if existing is None or _element_score(element) > _element_score(existing):
            deduped[lid] = element
    return list(deduped.values())


def transform(elements: list[dict]) -> pa.Table:
    elements = dedupe_elements(elements)
    landmark_ids, cities, names, geom_raws, lats, lons = [], [], [], [], [], []

    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue

        lid = _landmark_id(el)

        if el["type"] == "node":
            lat = el.get("lat")
            lon = el.get("lon")
        else:
            center = el.get("center", {})
            lat = center.get("lat")
            lon = center.get("lon")

        geom = make_point_wkt(lon, lat)

        landmark_ids.append(lid)
        cities.append("GBLON")
        names.append(name)
        geom_raws.append(geom)
        lats.append(lat)
        lons.append(lon)

    return pa.table(
        {
            "landmark_id": pa.array(landmark_ids, type=pa.string()),
            "city": pa.array(cities, type=pa.string()),
            "name": pa.array(names, type=pa.string()),
            "geometry_raw": pa.array(geom_raws, type=pa.string()),
            "latitude": pa.array(lats, type=pa.float64()),
            "longitude": pa.array(lons, type=pa.float64()),
        }
    )


def validate(table: pa.Table) -> None:
    validate_coordinates(table, city="London landmarks")


if __name__ == "__main__":
    elements = fetch_elements()
    table = transform(elements)
    validate(table)
    emit(table)

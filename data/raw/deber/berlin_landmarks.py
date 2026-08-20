#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

"""
Fetch Berlin historic sites from Overpass API (OpenStreetMap) and emit Arrow IPC to stdout.

Source: OpenStreetMap via Overpass API
Tag filter: historic=* (any value) with a name tag, within Berlin bounding box
~9,000 records using stable OSM element IDs.

Field mapping:
  OSM element id                   -> landmark_id (prefixed "deber-")
  name                             -> name
  lat/lon or center lat/lon        -> latitude, longitude, geometry_raw
"""

import os
import sys
import pyarrow as pa
from pathlib import Path
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

# Berlin bounding box (south, west, north, east)
BBOX = "52.3,13.0,52.7,13.8"

QUERY = f"""
[out:json][timeout:180];
(
  node["historic"]["name"]({BBOX});
  way["historic"]["name"]({BBOX});
);
out center;
"""


def fetch_elements() -> list[dict]:
    payload = post_json_with_retry(OVERPASS_URL, data={"data": QUERY}, timeout=240, headers=OVERPASS_HEADERS)
    return payload["elements"]


def transform(elements: list[dict]) -> pa.Table:
    landmark_ids, cities, names, geom_raws, lats, lons = [], [], [], [], [], []

    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue

        lid = f"deber-{el['id']}"

        if el["type"] == "node":
            lat = el.get("lat")
            lon = el.get("lon")
        else:
            center = el.get("center", {})
            lat = center.get("lat")
            lon = center.get("lon")

        geom = make_point_wkt(lon, lat)

        landmark_ids.append(lid)
        cities.append("DEBER")
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
    validate_coordinates(table, city="Berlin landmarks")


if __name__ == "__main__":
    elements = fetch_elements()
    table = transform(elements)
    validate(table)
    emit(table)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["requests"]
# ///
"""Download a US city's boundary into data/raw/boundaries/{code}.json.

    cd data/raw && uv run tools/fetch_boundary.py --code USCAM --state 25 --name Cambridge
    cd data/raw && uv run tools/fetch_boundary.py --code USSEA --at -122.3321,47.6062

The source is the Census Bureau's TIGERweb: public domain, and each layer is
one tessellation for the whole country (so two neighbours' polygons share
their edge exactly).

- `--state`/`--name` read the county-subdivision layer: in New England the
  subdivision *is* the town.  `--state` is the two-digit state FIPS code and
  `--name` the town's BASENAME there.
- `--at LON,LAT` (the city's `cityConfig.json` centre) is everywhere else:
  the consolidated city, else the incorporated place, that contains the
  point.  Places never overlap one another, but a place and a county
  subdivision can: neighbours must come from the same layer.

A city outside the US needs its own source; write the same one-feature file
by hand and record where it came from in the properties.

Workstation only: the file is committed, and nothing fetches it at build time.
After adding one, write the polygon city's CITY_TERRITORY box from the line
this prints, then run `tools/sync_osm_territory.py`.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path

import requests

RAW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW))

from shared.boundary import Boundary, boundary_path, rings_of  # noqa: E402

SERVICE_URL = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Places_CouSub_ConCity_SubMCD/MapServer"
LAYER_URL = f"{SERVICE_URL}/1/query"
# The current vintage's layers that hold a city, tried in this order by --at.
PLACE_LAYERS = {3: "Consolidated Cities", 4: "Incorporated Places"}
HEADERS = {"User-Agent": "urban-tree-reporting boundary fetch"}


def fetch(state: str, name: str) -> dict:
    if not state.isdigit() or "'" in name:
        raise SystemExit("--state is a FIPS number and --name carries no quote")
    response = requests.get(
        LAYER_URL,
        params={
            "where": f"STATE='{state}' AND BASENAME='{name}'",
            "outFields": "GEOID,BASENAME,NAME",
            "outSR": 4326,
            "geometryPrecision": 6,
            "f": "geojson",
        },
        headers=HEADERS,
        timeout=120,
    )
    response.raise_for_status()
    features = response.json().get("features") or []
    if len(features) != 1:
        found = [f["properties"].get("NAME") for f in features]
        raise SystemExit(f"expected one subdivision named {name!r} in state {state}, found {found}")
    return features[0], "County Subdivisions", LAYER_URL


def fetch_at(longitude: float, latitude: float) -> tuple[dict, str, str]:
    """The consolidated city, else the incorporated place, containing the point."""
    for layer, layer_name in PLACE_LAYERS.items():
        url = f"{SERVICE_URL}/{layer}/query"
        response = requests.get(
            url,
            params={
                "geometry": f"{longitude},{latitude}",
                "geometryType": "esriGeometryPoint",
                "inSR": 4326,
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "GEOID,BASENAME,NAME",
                "outSR": 4326,
                "geometryPrecision": 6,
                "f": "geojson",
            },
            headers=HEADERS,
            timeout=120,
        )
        response.raise_for_status()
        features = response.json().get("features") or []
        if len(features) > 1:
            raise SystemExit(f"{len(features)} {layer_name.lower()} contain {longitude},{latitude}")
        if features:
            return features[0], layer_name, url
    raise SystemExit(f"no consolidated city or incorporated place contains {longitude},{latitude}")


def envelope(boundary: Boundary, places: int = 4) -> tuple[float, float, float, float]:
    """The boundary's box rounded outward: the city's CITY_TERRITORY rectangle."""
    scale = 10**places
    lat_min, lat_max, lon_min, lon_max = boundary.box
    return (
        math.floor(lat_min * scale) / scale,
        math.ceil(lat_max * scale) / scale,
        math.floor(lon_min * scale) / scale,
        math.ceil(lon_max * scale) / scale,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--code", required=True, help="Five-letter city code, e.g. USCAM")
    ap.add_argument("--state", help="Two-digit state FIPS code, e.g. 25 (with --name)")
    ap.add_argument("--name", help="The town's county-subdivision BASENAME in TIGER, e.g. Cambridge")
    ap.add_argument("--at", help="LON,LAT inside the city: its consolidated city or incorporated place")
    args = ap.parse_args()

    if args.at:
        longitude, latitude = (float(v) for v in args.at.split(","))
        feature, layer_name, url = fetch_at(longitude, latitude)
    elif args.state and args.name:
        feature, layer_name, url = fetch(args.state, args.name)
    else:
        raise SystemExit("pass --at LON,LAT, or --state and --name")
    boundary = Boundary(rings_of(feature["geometry"]))
    document = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "city": args.code.upper(),
                    "name": feature["properties"]["NAME"],
                    "geoid": feature["properties"]["GEOID"],
                    "source": f"US Census Bureau TIGERweb, {layer_name} (public domain)",
                    "source_url": url,
                    "retrieved": date.today().isoformat(),
                },
                "geometry": feature["geometry"],
            }
        ],
    }
    path = boundary_path(args.code)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(document, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
    corners = sum(len(ring) - 1 for ring in boundary.rings)
    print(f"{path.relative_to(RAW).as_posix()}: {feature['properties']['NAME']}, {corners} corners")
    print(f"    '{args.code.upper()}': ({envelope(boundary)},),")


if __name__ == "__main__":
    main()

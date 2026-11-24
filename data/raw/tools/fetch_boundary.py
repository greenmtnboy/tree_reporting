#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["requests"]
# ///
"""Download a US town's boundary into data/raw/boundaries/{code}.json.

    cd data/raw && uv run tools/fetch_boundary.py --code USCAM --state 25 --name Cambridge

The source is the Census Bureau's TIGERweb county-subdivision layer: public
domain, one tessellation for the whole country (so two neighbours' polygons
share their edge exactly), and in New England the subdivision *is* the town.
`--state` is the two-digit state FIPS code and `--name` the town's BASENAME
there.  A city outside the US needs its own source; write the same one-feature
file by hand and record where it came from in the properties.

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

LAYER_URL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
    "Places_CouSub_ConCity_SubMCD/MapServer/1/query"
)


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
        headers={"User-Agent": "urban-tree-reporting boundary fetch"},
        timeout=120,
    )
    response.raise_for_status()
    features = response.json().get("features") or []
    if len(features) != 1:
        found = [f["properties"].get("NAME") for f in features]
        raise SystemExit(f"expected one subdivision named {name!r} in state {state}, found {found}")
    return features[0]


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
    ap.add_argument("--state", required=True, help="Two-digit state FIPS code, e.g. 25")
    ap.add_argument("--name", required=True, help="The town's BASENAME in TIGER, e.g. Cambridge")
    args = ap.parse_args()

    feature = fetch(args.state, args.name)
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
                    "source": "US Census Bureau TIGERweb, County Subdivisions (public domain)",
                    "source_url": LAYER_URL,
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

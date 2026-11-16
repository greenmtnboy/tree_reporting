#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Wodonga's places on the Victorian Heritage Register.

Source: the Victorian Heritage Register layer
(`open-data-platform:heritage_register`) on Vicmap's open-data GeoServer,
opendata.maps.vic.gov.au -- the state's official designation register under
the Heritage Act 2017, published by the Department of Transport and Planning
under CC BY 4.0 (data.vic.gov.au dataset `victorian-heritage-register`).
The same reader as Ballarat's (`aubal/ballarat_landmarks.py`), with the City
of Wodonga's boundary from Vicmap Admin (`ad_lga_area_polygon`, `WODONGA
CITY`) in place of Ballarat's.

It is a short list: on 2026-11-16 the LGA's bounding box held 6 register
parcels, and 4 places lie inside the boundary -- Cambourne Park Homestead,
De Kerrilleau Homestead, Block 19 Bonegilla and Clyde Cameron College.  The
two left out are The Hermitage (Indigo Shire, just west of the city) and the
Bethanga Bridge (Towong Shire).

The council's own "Wodonga heritage implementation study public" layer
(ArcGIS item 0911c085dc5b4fdeaecaf54fabc18b39, 61 points with names and
addresses) is richer, but it is not used: it is a consultation layer of
*proposed* heritage-overlay places rather than a designation register, and
the item publishes no licence (`licenseInfo` and `accessInformation` are
both empty), unlike the tree layers' CC BY 4.0.

Mapped: `landmark_id` = `wod-{vhr_num}`; `name` = `site_name` in
capitalised words (`display_name`); `register_number` = `vhr_num`; `lm_type`
= "Victorian Heritage Register"; `geometry_raw` = the parcels inside the
city as a WKT MULTIPOLYGON.
"""

import string
import sys
from collections import defaultdict
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.wfs import WfsLayer, iter_wfs_features
from shared.ingest import emit

VICMAP_WFS = "https://opendata.maps.vic.gov.au/geoserver/wfs"
REGISTER = WfsLayer(VICMAP_WFS, "open-data-platform:heritage_register")
LGA = WfsLayer(VICMAP_WFS, "open-data-platform:ad_lga_area_polygon")

CITY_CODE = "AUWOD"
ID_PREFIX = "wod"
LGA_NAME = "WODONGA CITY"
REGISTER_NAME = "Victorian Heritage Register"

Ring = list[list[float]]
Polygon = list[Ring]


def polygons_of(geometry: dict | None) -> list[Polygon]:
    """A GeoJSON Polygon or MultiPolygon as a list of polygons (rings of [x, y])."""
    if not geometry:
        return []
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "Polygon":
        return [coords]
    if kind == "MultiPolygon":
        return list(coords)
    return []


def _in_ring(x: float, y: float, ring: Ring) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def in_area(x: float, y: float, area: list[Polygon]) -> bool:
    """Inside any polygon's exterior ring and outside its holes."""
    for polygon in area:
        if polygon and _in_ring(x, y, polygon[0]) and not any(
            _in_ring(x, y, hole) for hole in polygon[1:]
        ):
            return True
    return False


def vertex_centroid(polygon: Polygon) -> tuple[float, float]:
    """The mean of the exterior ring's vertices -- enough for a parcel."""
    ring = polygon[0][:-1] or polygon[0]
    return (
        sum(pt[0] for pt in ring) / len(ring),
        sum(pt[1] for pt in ring) / len(ring),
    )


def polygon_wkt(polygon: Polygon) -> str:
    return "(" + ", ".join(
        "(" + ", ".join(f"{pt[0]} {pt[1]}" for pt in ring) + ")" for ring in polygon
    ) + ")"


def city_boundary() -> list[Polygon]:
    area: list[Polygon] = []
    for page in iter_wfs_features(
        LGA, sort_by="ufi", cql_filter=f"official_name = '{LGA_NAME}'"
    ):
        for feature in page:
            area.extend(polygons_of(feature.get("geometry")))
    if not area:
        raise RuntimeError(f"Vicmap returned no boundary for {LGA_NAME}")
    return area


def bbox(area: list[Polygon]) -> tuple[float, float, float, float]:
    xs = [pt[0] for polygon in area for pt in polygon[0]]
    ys = [pt[1] for polygon in area for pt in polygon[0]]
    return min(xs), min(ys), max(xs), max(ys)


# The register writes names in capitals.  They are published in capitalised
# words, with short joining words lower case and initialisms kept as written.
SMALL_WORDS = frozenset({"a", "and", "at", "for", "in", "of", "on", "the", "to"})
INITIALISMS = frozenset({"ANZ", "AMP", "CBC", "CWA", "ES&A", "MLC", "RAAF", "RSL", "SEC", "YMCA"})


def display_name(value: str | None) -> str | None:
    """`FORMER ES&A BANK OF VICTORIA` -> `Former ES&A Bank of Victoria`."""
    words = (value or "").split()
    if not words:
        return None
    out = []
    for i, word in enumerate(words):
        if word.upper() in INITIALISMS:
            out.append(word.upper())
        elif i and word.lower() in SMALL_WORDS:
            out.append(word.lower())
        else:
            out.append(string.capwords(word.lower()))
    return " ".join(out)


def build() -> pa.Table:
    area = city_boundary()
    xmin, ymin, xmax, ymax = bbox(area)
    places: dict[str, dict] = {}
    parts: dict[str, list[Polygon]] = defaultdict(list)
    for page in iter_wfs_features(
        REGISTER,
        sort_by="ufi",
        properties=["ufi", "vhr_num", "site_name", "geom"],
        cql_filter=f"BBOX(geom, {xmin}, {ymin}, {xmax}, {ymax}, 'EPSG:4326')",
    ):
        for feature in page:
            props = feature.get("properties") or {}
            number = (props.get("vhr_num") or "").strip()
            name = display_name(props.get("site_name"))
            if not number or not name:
                continue
            inside = [
                polygon
                for polygon in polygons_of(feature.get("geometry"))
                if polygon and in_area(*vertex_centroid(polygon), area)
            ]
            if not inside:
                continue
            places.setdefault(number, {"name": name})
            parts[number].extend(inside)

    numbers = sorted(places)
    if not numbers:
        raise RuntimeError(f"No Victorian Heritage Register places inside {LGA_NAME}")
    return pa.table(
        {
            "landmark_id": pa.array([f"{ID_PREFIX}-{n}" for n in numbers], type=pa.string()),
            "city": pa.array([CITY_CODE] * len(numbers), type=pa.string()),
            "name": pa.array([places[n]["name"] for n in numbers], type=pa.string()),
            "geometry_raw": pa.array(
                [
                    "MULTIPOLYGON(" + ", ".join(polygon_wkt(p) for p in parts[n]) + ")"
                    for n in numbers
                ],
                type=pa.string(),
            ),
            "lm_type": pa.array([REGISTER_NAME] * len(numbers), type=pa.string()),
            "register_number": pa.array(numbers, type=pa.string()),
        }
    )


if __name__ == "__main__":
    table = build()
    print(f"{CITY_CODE} landmarks: {table.num_rows} heritage places", file=sys.stderr)
    emit(table)

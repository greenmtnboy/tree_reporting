#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Ballarat's places on the Victorian Heritage Register.

Source: the Victorian Heritage Register layer
(`open-data-platform:heritage_register`) on Vicmap's open-data GeoServer,
opendata.maps.vic.gov.au -- the state's official designation register under
the Heritage Act 2017, published by the Department of Transport and Planning
under CC BY 4.0 (data.vic.gov.au dataset `victorian-heritage-register`).
That is the first preference in the runbook's landmark source order.  The
City of Ballarat's own portal has no heritage register (its catalogue holds
the Exceptional Tree Register, the Avenue of Honour plaques and heritage-area
gutter rails, none of them a register of places).

The layer is statewide, one parcel polygon per row, keyed by `vhr_num`
(`H2089`) with the place's `site_name` in capitals.  It carries no council
attribute, so the City of Ballarat is cut out with the LGA's own boundary
from Vicmap Admin (`ad_lga_area_polygon`, `BALLARAT CITY`): the register is
read inside the LGA's bounding box, and a parcel is kept when its centroid
falls inside the boundary.  A box alone would take in Creswick (Hepburn
Shire) and Smythesdale (Golden Plains Shire).  A place registered
over several parcels -- the Ballarat Railway Complex, the Avenue of Honour
and Arch of Victory -- becomes one landmark whose geometry is the
MULTIPOLYGON of its parcels inside the city; a serial listing (the Glenelg
Highway stone mileposts) keeps only its Ballarat parcels.  On 2026-11-16 the
LGA's box held 104 register rows; 83 parcels lay inside the boundary, making
67 places.  The 22 parcels left out are Creswick's (town hall, gold office,
railway station, School of Forestry, its own Avenue of Honour), Smythesdale's
court house and the Glenelg Highway mileposts in Golden Plains.

Mapped: `landmark_id` = `bal-{vhr_num}`; `name` = `site_name` in
capitalised words (`display_name`); `register_number` = `vhr_num`, the extra
Prague's register already declares in `landmark_common.preql`; `lm_type` =
"Victorian Heritage Register", Naples' convention for naming the register;
`geometry_raw` = the parcels as WKT.  Adjacent parcels of one place are
emitted as separate members of the MULTIPOLYGON rather than dissolved (3 of
67 places, which a strict validity check flags; their centroid is
unaffected).  `heritage_object` (Y on movable objects such as the Eureka
Flag) is not published.
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

CITY_CODE = "AUBAL"
ID_PREFIX = "bal"
LGA_NAME = "BALLARAT CITY"
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

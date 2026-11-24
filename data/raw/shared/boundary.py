"""A city's real boundary, for a territory a rectangle cannot draw.

`CITY_TERRITORY` in `shared/ingest.py` is rectangles, which is enough for a
city alone on the map and for neighbours a river separates.  It is not enough
for towns that interlock street by street (Cambridge and Somerville, Brookline
inside Boston): a staircase of rectangles fine enough to follow them is
hundreds of boxes, slower to test and still a few metres wrong.  A city with
a file in `data/raw/boundaries/` is its polygon instead:

    boundaries/{code}.geojson   one Feature, a Polygon or MultiPolygon in
                                WGS84 (longitude, latitude), with where it
                                came from in its properties

Dropping a file there is the whole registration.  `shared.ingest` reads the
polygon for `in_city_territory`, carves it out of any rectangle city whose
box it sits in, and `tools/sync_osm_territory.py` writes it into the city
models as `geo_contains` (`boundaries.preql`).  `tools/fetch_boundary.py`
downloads one from the US Census.

No dependency beyond the standard library: this is imported by every ingest,
and the predicted ingest asks it about millions of rows, so `contains` reads
only the edges that cross the point's latitude band.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

BOUNDARY_DIR = Path(__file__).resolve().parents[1] / "boundaries"

Ring = list[tuple[float, float]]  # (longitude, latitude), closed
Edge = tuple[float, float, float, float]  # lon0, lat0, lon1, lat1


class Boundary:
    """One city's polygon: even-odd over every ring, so a hole is outside."""

    def __init__(self, rings: list[Ring]):
        if not rings or any(len(ring) < 4 or ring[0] != ring[-1] for ring in rings):
            raise ValueError("a boundary needs closed rings of at least three corners")
        self.rings = rings
        lons = [lon for ring in rings for lon, _ in ring]
        lats = [lat for ring in rings for _, lat in ring]
        self.lat_min, self.lat_max = min(lats), max(lats)
        self.lon_min, self.lon_max = min(lons), max(lons)
        edges = [
            (a[0], a[1], b[0], b[1])
            for ring in rings
            for a, b in zip(ring, ring[1:])
            if a[1] != b[1]  # a level edge crosses no scanline
        ]
        # About one edge per band on average; each band lists the edges whose
        # latitude span reaches into it.
        self._band_count = max(1, len(edges))
        self._band_height = (self.lat_max - self.lat_min) / self._band_count or 1.0
        self._bands: list[list[Edge]] = [[] for _ in range(self._band_count)]
        for edge in edges:
            low, high = sorted((edge[1], edge[3]))
            for band in range(self._band_of(low), self._band_of(high) + 1):
                self._bands[band].append(edge)

    def _band_of(self, latitude: float) -> int:
        return min(self._band_count - 1, max(0, int((latitude - self.lat_min) / self._band_height)))

    @property
    def box(self) -> tuple[float, float, float, float]:
        """(lat_min, lat_max, lon_min, lon_max), the order CITY_BOUNDS uses."""
        return (self.lat_min, self.lat_max, self.lon_min, self.lon_max)

    def contains(self, latitude: float, longitude: float) -> bool:
        """Whether the point is inside.  A point on an edge two polygons share
        belongs to exactly one of them (the crossing test is half-open)."""
        if not (self.lat_min <= latitude <= self.lat_max and self.lon_min <= longitude <= self.lon_max):
            return False
        inside = False
        for lon0, lat0, lon1, lat1 in self._bands[self._band_of(latitude)]:
            if (lat0 <= latitude) != (lat1 <= latitude) and longitude < lon0 + (latitude - lat0) * (
                lon1 - lon0
            ) / (lat1 - lat0):
                inside = not inside
        return inside

    def wkt(self) -> str:
        """The polygon as WKT, the text `geo_from_text` reads."""

        def ring_text(ring: Ring) -> str:
            return "(" + ", ".join(f"{lon:.6f} {lat:.6f}" for lon, lat in ring) + ")"

        polygons = _polygons(self.rings)
        if len(polygons) == 1:
            return "POLYGON(" + ", ".join(ring_text(r) for r in polygons[0]) + ")"
        return "MULTIPOLYGON(" + ", ".join("(" + ", ".join(ring_text(r) for r in p) + ")" for p in polygons) + ")"


def _signed_area(ring: Ring) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:])) / 2


def _polygons(rings: list[Ring]) -> list[list[Ring]]:
    """Rings grouped as GeoJSON orders them: an exterior (counter-clockwise),
    then its holes (clockwise)."""
    polygons: list[list[Ring]] = []
    for ring in rings:
        if _signed_area(ring) > 0 or not polygons:
            polygons.append([ring])
        else:
            polygons[-1].append(ring)
    return polygons


def rings_of(geometry: dict) -> list[Ring]:
    """Every ring of a GeoJSON Polygon or MultiPolygon, exterior first and
    wound the GeoJSON way so `_polygons` can regroup them."""
    kind = geometry.get("type")
    if kind == "Polygon":
        polygons = [geometry["coordinates"]]
    elif kind == "MultiPolygon":
        polygons = geometry["coordinates"]
    else:
        raise ValueError(f"a boundary is a Polygon or MultiPolygon, not {kind!r}")
    rings: list[Ring] = []
    for polygon in polygons:
        for index, coordinates in enumerate(polygon):
            ring = [(float(lon), float(lat)) for lon, lat, *_ in coordinates]
            if (_signed_area(ring) > 0) != (index == 0):
                ring.reverse()
            rings.append(ring)
    return rings


def boundary_path(city_code: str) -> Path:
    return BOUNDARY_DIR / f"{city_code.lower()}.geojson"


def boundary_codes() -> tuple[str, ...]:
    """Every city with a boundary file, sorted."""
    return tuple(sorted(path.stem.upper() for path in BOUNDARY_DIR.glob("*.geojson")))


@lru_cache(maxsize=None)
def city_boundary(city_code: str) -> Boundary | None:
    """The city's polygon, or None for a city that is rectangles only."""
    path = boundary_path(city_code)
    if not path.exists():
        return None
    document = json.loads(path.read_text(encoding="utf-8"))
    features = document["features"] if document.get("type") == "FeatureCollection" else [document]
    if len(features) != 1:
        raise ValueError(f"{path.name} must hold exactly one feature, found {len(features)}")
    return Boundary(rings_of(features[0]["geometry"]))

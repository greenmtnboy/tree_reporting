"""A city's real boundary, for a territory a rectangle cannot draw.

`CITY_TERRITORY` in `shared/ingest.py` is rectangles, which is enough for
neighbours a river separates.  It is not enough for towns that interlock
street by street (Cambridge and Somerville, Brookline inside Boston): a
staircase of rectangles fine enough to follow them is hundreds of boxes,
slower to test and still a few metres wrong.  Nor for a city alone on the
map, whose rectangle takes in its suburbs' OSM trees.  A city with a file in
`data/raw/boundaries/` is its polygon instead:

    boundaries/{code}.json   one Feature, a Polygon or MultiPolygon in
                                WGS84 (longitude, latitude), with where it
                                came from in its properties

Dropping a file there is the registration.  `shared.ingest` reads the
polygon for `in_city_territory`, carves it out of any rectangle city whose
box it sits in, and the OSM extract keeps only the nodes inside it.
`tools/fetch_boundary.py` downloads one from the US Census.

**The files never reach a worker.**  Every polygon is a few tens of kilobytes
and the cloud sync caps its body, so the directory is excluded from the
bundle (`[cloud] exclude` in trilogy.toml).  `tools/publish_boundaries.py`
uploads them as one parquet named by their content hash and writes that name
into `BOUNDARY_PARQUET` below; a worker, which has no `boundaries/` directory,
reads the parquet, and a checkout reads the files.  The hash keeps the two
the same: `test_the_published_boundaries_are_the_files` fails until the files
are republished.  The parquet also carries each polygon as WKT, for anything
outside Python that wants the lines.

No dependency beyond the standard library (pyarrow only on a worker, to read
the parquet): this is imported by every ingest,
and the predicted ingest asks it about millions of rows, so `contains` reads
only the edges that cross the point's latitude band.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
import urllib.request
from functools import lru_cache
from pathlib import Path

BOUNDARY_DIR = Path(__file__).resolve().parents[1] / "boundaries"

# Written by tools/publish_boundaries.py --publish; do not edit by hand.
BOUNDARY_PARQUET = "boundaries_0f9627a7eb1b.parquet"
BOUNDARY_PUBLIC_PREFIX = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees/reference"

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
    return BOUNDARY_DIR / f"{city_code.lower()}.json"


def boundary_files() -> list[Path]:
    return sorted(BOUNDARY_DIR.glob("*.json"))


def boundary_feature(path: Path) -> dict:
    """The one Feature a boundary file holds."""
    document = json.loads(path.read_text(encoding="utf-8"))
    features = document["features"] if document.get("type") == "FeatureCollection" else [document]
    if len(features) != 1:
        raise ValueError(f"{path.name} must hold exactly one feature, found {len(features)}")
    return features[0]


def boundary_digest(files: list[Path] | None = None) -> str:
    """The first 12 hex digits of a sha256 over every file's name and bytes,
    line endings folded to LF so a Windows checkout and CI agree."""
    digest = hashlib.sha256()
    for path in files if files is not None else boundary_files():
        digest.update(path.name.encode() + b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()[:12]


def published_boundary_name(files: list[Path] | None = None) -> str:
    return f"boundaries_{boundary_digest(files)}.parquet"


def _published_geometries() -> dict[str, dict]:
    """The published parquet's polygons, by city: what a worker reads."""
    import pyarrow.parquet as pq

    url = os.environ.get("BOUNDARY_PARQUET_URL") or f"{BOUNDARY_PUBLIC_PREFIX}/{BOUNDARY_PARQUET}"
    request = urllib.request.Request(url, headers={"User-Agent": "urban-tree-reporting boundaries"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = response.read()
            break
        except OSError:
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))
    table = pq.read_table(io.BytesIO(data), columns=["city", "geometry"])
    return {city: json.loads(geometry) for city, geometry in zip(*(table[c].to_pylist() for c in ("city", "geometry")))}


@lru_cache(maxsize=1)
def _geometries() -> dict[str, dict]:
    files = boundary_files()
    if files:
        return {path.stem.upper(): boundary_feature(path)["geometry"] for path in files}
    return _published_geometries()


def boundary_codes() -> tuple[str, ...]:
    """Every city with a boundary, sorted."""
    return tuple(sorted(_geometries()))


@lru_cache(maxsize=None)
def city_boundary(city_code: str) -> Boundary | None:
    """The city's polygon, or None for a city that is rectangles only."""
    geometry = _geometries().get(city_code.upper())
    return None if geometry is None else Boundary(rings_of(geometry))

"""Read a point layer out of a public CARTO named map, as vector tiles.

NOT a uv inline script -- a regular importable module, like `shared.platforms.arcgis`.

MapGeo (`{town}.mapgeo.io`) draws its themes from CARTO named maps on its own
tiler (`mapstudio.mapgeo.io`), and Watertown's 2022 tree inventory is one of
them.  The app has no export for a theme, and the tiler's SQL API refuses an
anonymous query, but a named map is public by design: instantiating it needs
no key, and the layergroup it returns serves Mapbox vector tiles whose
features carry every column of the layer's query.  So a sweep of tiles over
the town is a full read.

Three things here are correctness rather than convenience:

* **Instantiate per run.**  The layergroup id names one revision of the
  template, so a cached id goes stale the day the map is edited.
* **Features repeat across tiles.**  A point near a tile edge sits in the
  buffer of its neighbours, so rows are keyed on `cartodb_id`, never counted
  per tile.
* **Coordinates come from the tile, not a column.**  A tile's points are
  integers on its `extent` grid; at z14 and the usual 4096 that is ~0.6m in
  Massachusetts, inside the published precision of any inventory here.

The tile is decoded here rather than with `mapbox-vector-tile`, so the
ingest needs nothing beyond `requests`: only point features are read, and
the format's protobuf subset is a few dozen lines.

    from shared.platforms.carto import NamedMap, fetch_points

    MAP = NamedMap("https://mapstudio.mapgeo.io/user/mapgeoprod",
                   "tpl_4341bef6_c15b_11ed_bde7_8ec690ec29cb")
    rows = fetch_points(MAP, bbox=(42.34, 42.39, -71.21, -71.13))
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

# This module is imported as `shared.platforms.carto`, and running a script
# from `shared/platforms` puts that directory on the path rather than
# `data/raw`, so the package import has to be bootstrapped.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared.ingest import get_with_retry, post_json_with_retry  # noqa: E402

# Zoom 14 tiles are ~2.4 km across here: a town is a dozen requests, and
# Watertown's 9,116 sites read the same at z14 as at z15 (2026-10-27), so no
# tile is capped at this size.
SWEEP_ZOOM = 14


@dataclass(frozen=True)
class NamedMap:
    """One named-map template on a CARTO (Windshaft) tiler.

    *user_url* is the tiler's `/user/{user}` base; *layer* is the mapnik
    layer's index in the layergroup (index 0 is usually the basemap).
    """

    user_url: str
    template: str
    layer: int = 1
    params: tuple[tuple[str, int], ...] = (("layer0", 1),)


def instantiate(named: NamedMap) -> str:
    """The layergroup id for the template's current revision."""
    payload = post_json_with_retry(
        f"{named.user_url}/api/v1/map/named/{named.template}",
        data=json.dumps(dict(named.params)),
        headers={"Content-Type": "application/json"},
        timeout=60,
        backoff=2.0,
    )
    return payload["layergroupid"]


def tile_for(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    n = 2**zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return x, y


def fetch_tile(named: NamedMap, layergroup: str, z: int, x: int, y: int) -> bytes:
    return get_with_retry(
        f"{named.user_url}/api/v1/map/{layergroup}/{named.layer}/{z}/{x}/{y}.mvt"
    ).content


def fetch_points(
    named: NamedMap,
    *,
    bbox: tuple[float, float, float, float],
    zoom: int = SWEEP_ZOOM,
    key: str = "cartodb_id",
) -> list[dict]:
    """Every point feature in *bbox* (lat_min, lat_max, lon_min, lon_max),
    one dict per distinct *key*, with `longitude` and `latitude` added."""
    lat_min, lat_max, lon_min, lon_max = bbox
    layergroup = instantiate(named)
    x0, y0 = tile_for(lon_min, lat_max, zoom)
    x1, y1 = tile_for(lon_max, lat_min, zoom)
    rows: dict = {}
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            for row in decode_points(fetch_tile(named, layergroup, zoom, x, y), zoom, x, y):
                rows[row[key]] = row
    return list(rows.values())


# ---------------------------------------------------------------------------
# Mapbox vector tile, points only
# ---------------------------------------------------------------------------
#
# Tile {layers = 3}; Layer {name = 1, features = 2, keys = 3, values = 4,
# extent = 5}; Feature {id = 1, tags = 2 (packed), type = 3, geometry = 4
# (packed)}; Value {string = 1, float = 2, double = 3, int64 = 4, uint64 = 5,
# sint64 = 6, bool = 7}.  https://github.com/mapbox/vector-tile-spec


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _fields(buf: bytes) -> Iterator[tuple[int, int, object]]:
    """(field number, wire type, value) for each field of one message."""
    i = 0
    while i < len(buf):
        tag, i = _varint(buf, i)
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            value, i = _varint(buf, i)
        elif wire == 1:
            value, i = buf[i : i + 8], i + 8
        elif wire == 2:
            size, i = _varint(buf, i)
            value, i = buf[i : i + size], i + size
        elif wire == 5:
            value, i = buf[i : i + 4], i + 4
        else:
            raise ValueError(f"MVT: unsupported wire type {wire}")
        yield field, wire, value


def _packed(buf: bytes) -> list[int]:
    out, i = [], 0
    while i < len(buf):
        value, i = _varint(buf, i)
        out.append(value)
    return out


def _zigzag(n: int) -> int:
    return (n >> 1) ^ -(n & 1)


def _value(buf: bytes):
    import struct

    for field, _, raw in _fields(buf):
        if field == 1:
            return raw.decode("utf-8")
        if field == 2:
            return struct.unpack("<f", raw)[0]
        if field == 3:
            return struct.unpack("<d", raw)[0]
        if field in (4, 5):
            return raw - (1 << 64) if field == 4 and raw >= 1 << 63 else raw
        if field == 6:
            return _zigzag(raw)
        if field == 7:
            return bool(raw)
    return None


def decode_points(tile: bytes, z: int, x: int, y: int) -> list[dict]:
    """The point features of every layer in *tile*, as property dicts with
    `longitude` and `latitude`.  Non-point features are skipped."""
    rows: list[dict] = []
    for field, _, layer_buf in _fields(tile):
        if field != 3:
            continue
        keys: list[str] = []
        values: list = []
        features: list[bytes] = []
        extent = 4096
        for f, _, raw in _fields(layer_buf):
            if f == 2:
                features.append(raw)
            elif f == 3:
                keys.append(raw.decode("utf-8"))
            elif f == 4:
                values.append(_value(raw))
            elif f == 5:
                extent = raw
        for feature in features:
            tags: list[int] = []
            geometry: list[int] = []
            kind = 0
            for f, _, raw in _fields(feature):
                if f == 2:
                    tags = _packed(raw)
                elif f == 3:
                    kind = raw
                elif f == 4:
                    geometry = _packed(raw)
            if kind != 1 or len(geometry) < 3:
                continue
            # MoveTo with a count of one, then a zigzag (dx, dy) from (0, 0).
            # A multipoint carries more pairs; only its first is a tree.
            px, py = _zigzag(geometry[1]), _zigzag(geometry[2])
            row = {keys[tags[i]]: values[tags[i + 1]] for i in range(0, len(tags), 2)}
            n = 2**z
            row["longitude"] = (x + px / extent) / n * 360.0 - 180.0
            row["latitude"] = math.degrees(
                math.atan(math.sinh(math.pi * (1 - 2 * (y + py / extent) / n)))
            )
            rows.append(row)
    return rows

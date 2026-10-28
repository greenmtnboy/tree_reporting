"""Watertown's MapGeo inventory: the vector-tile decoder in
`shared.platforms.carto`, and the sites and labels the ingest keeps or fixes.
Pinned without a network fetch."""

import importlib.util
import sys
from pathlib import Path

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from shared.platforms.carto import decode_points, tile_for  # noqa: E402


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, RAW_DIR / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


watertown = _load("watertown_tree_info", "usbos/watertown_tree_info.py")


# --- a hand-built tile -------------------------------------------------------


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _field(number: int, payload: bytes) -> bytes:
    return _varint(number << 3 | 2) + _varint(len(payload)) + payload


def _uint(number: int, value: int) -> bytes:
    return _varint(number << 3) + _varint(value)


def _zz(n: int) -> int:
    return (n << 1) ^ (n >> 63)


def _tile(points: list[tuple[int, int, dict]], extent: int = 4096) -> bytes:
    keys: list[str] = []
    values: list = []
    features = b""
    for px, py, props in points:
        tags = b""
        for k, v in props.items():
            if k not in keys:
                keys.append(k)
            if v not in values:
                values.append(v)
            tags += _varint(keys.index(k)) + _varint(values.index(v))
        geometry = _varint(1 << 3 | 1) + _varint(_zz(px)) + _varint(_zz(py))
        features += _field(2, _field(2, tags) + _uint(3, 1) + _field(4, geometry))
    layer = _field(1, b"layer0") + features
    for k in keys:
        layer += _field(3, k.encode())
    for v in values:
        if isinstance(v, str):
            layer += _field(4, _field(1, v.encode()))
        else:
            layer += _field(4, _uint(5, v))
    layer += _uint(5, extent)
    return _field(3, layer)


class TestDecode:
    def test_properties_and_position(self):
        z, (x, y) = 14, tile_for(-71.1776, 42.3721, 14)
        tile = _tile([(2048, 2048, {"cartodb_id": 7, "scientific": "Acer rubrum"})])
        (row,) = decode_points(tile, z, x, y)
        assert row["cartodb_id"] == 7
        assert row["scientific"] == "Acer rubrum"
        # The tile's centre, so within half a tile (~1.2 km) of the point.
        assert abs(row["longitude"] - -71.1776) < 0.02
        assert abs(row["latitude"] - 42.3721) < 0.02

    def test_tile_corner_is_the_tile_origin(self):
        (row,) = decode_points(_tile([(0, 0, {"cartodb_id": 1})]), 1, 1, 0)
        assert (round(row["longitude"], 6), round(row["latitude"], 6)) == (0.0, 85.051129)


# --- rows -------------------------------------------------------------------


def _row(primaryid, scientific, status="Alive", common="Red maple", dbh=8):
    return {
        "primaryid": primaryid,
        "scientific": scientific,
        "commonname": common,
        "status": status,
        "dbh": dbh,
        "latitude": 42.37,
        "longitude": -71.18,
    }


class TestWatertown:
    def test_only_standing_trees_are_kept(self):
        table, dropped = watertown.build_table(
            [
                _row(1, "Acer rubrum"),
                _row(2, "Acer rubrum", status="Dead"),
                _row(3, "NULL", status="Proposed Site - Small", common="Proposed Planting Site"),
                _row(4, "NULL", status="Stump", common="Stump"),
                _row(5, "NULL", common="Stump"),
            ]
        )
        assert table.column("tree_id").to_pylist() == ["wat-1", "wat-2"]
        assert dropped == {"no species": 0, "not a tree": 3}

    def test_unreadable_scientific_names_are_fixed(self):
        table, _ = watertown.build_table(
            [_row(1, "Elm", common="Elm"), _row(2, "Gingko biloba, female", common="Female ginkgo")]
        )
        assert table.column("species").to_pylist() == ["Ulmus", "Ginkgo biloba"]

    def test_a_zero_diameter_is_unmeasured(self):
        table, _ = watertown.build_table([_row(1, "Acer rubrum", dbh=0)])
        assert table.column("diameter_at_breast_height").to_pylist() == [None]

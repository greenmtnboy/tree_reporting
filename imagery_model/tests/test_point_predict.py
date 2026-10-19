import json
from types import SimpleNamespace

import numpy as np
import pytest

from urban_tree_ml import point_predict
from urban_tree_ml.point_predict import (
    MultiInventory,
    cities_meeting,
    city_boxes,
    grid_tile_origin,
    owning_city,
    point_chip_id,
)


def test_grid_tile_keeps_the_point_a_quarter_tile_from_the_edge():
    tile = 153.6
    for x, y in [(326_170.0, 4_689_700.0), (326_247.0, 4_689_639.0), (0.1, -0.1)]:
        west, north = grid_tile_origin(x, y, tile)
        assert west + tile / 4 - 1e-6 <= x <= west + 3 * tile / 4 + 1e-6
        assert north - 3 * tile / 4 - 1e-6 <= y <= north - tile / 4 + 1e-6


def test_nearby_points_share_a_tile():
    assert grid_tile_origin(1000.0, 2000.0, 153.6) == grid_tile_origin(1010.0, 1990.0, 153.6)


def test_chip_id_names_the_grid_cell():
    assert point_chip_id(26919, 326170.4, 4689792.0) == "p26919_e326170_n4689792"


def test_city_boxes_are_read_from_the_ingest_library():
    envelopes, territory = city_boxes()
    assert "USBOS" in envelopes and "USSFO" in envelopes
    assert all(len(box) == 4 for box in envelopes.values())
    assert all(boxes for boxes in territory.values())


def test_owning_city_prefers_carved_territory():
    envelopes = {"AAAAA": (0.0, 2.0, 0.0, 2.0), "BBBBB": (1.0, 3.0, 1.0, 3.0)}
    territory = {"BBBBB": ((1.5, 3.0, 1.5, 3.0),)}
    assert owning_city(1.6, 1.6, envelopes, territory) == "BBBBB"
    assert owning_city(1.2, 1.2, envelopes, territory) == "AAAAA"
    assert owning_city(5.0, 5.0, envelopes, territory) is None


def test_a_tile_on_a_boundary_meets_both_cities():
    envelopes = {"AAAAA": (0.0, 1.0, 0.0, 1.0), "BBBBB": (0.0, 1.0, 1.0, 2.0), "CCCCC": (5.0, 6.0, 5.0, 6.0)}
    bounds = {"south": 0.4, "north": 0.5, "west": 0.95, "east": 1.05}
    assert cities_meeting(bounds, envelopes) == ["AAAAA", "BBBBB"]


class _Reader:
    def __init__(self, version, trees):
        self.version, self.trees = version, trees

    def within(self, bounds):
        return self.trees


def test_multi_inventory_merges_cities_once_per_tree():
    inventory = MultiInventory({
        "BBBBB": _Reader("v2", [{"treeId": "b1"}, {"treeId": "shared"}]),
        "AAAAA": _Reader("v1", [{"treeId": "a1"}, {"treeId": "shared"}]),
    })
    assert [t["treeId"] for t in inventory.within({})] == ["a1", "b1", "shared"]
    assert inventory.version == "AAAAA:v1;BBBBB:v2"


def _cache_tile(artifacts, year, chip_id, west, north):
    stem = artifacts / "point-tiles" / f"naip{year}" / f"{chip_id}_30cm"
    point_predict.write_cached_tile(
        stem.with_suffix(".tif"), np.zeros((4, 8, 8), dtype=np.uint8),
        crs="EPSG:26919", west=west, north=north, resolution_m=0.3,
    )
    stem.with_name(stem.name + ".manifest.json").write_text(json.dumps({
        "collection": "naip", "year": str(year), "valid_fraction": 1.0,
        "sources": [{"item_id": f"item-{year}", "acquisition_datetime": f"{year}-07-01T00:00:00+00:00"}],
    }), encoding="utf-8")


def test_a_hinted_cached_tile_is_predicted_without_a_catalog_search(tmp_path, monkeypatch):
    def search(*args, **kwargs):
        raise AssertionError("searched STAC for a cached tile")

    monkeypatch.setattr(point_predict, "search_items", search)
    predictor = SimpleNamespace(
        chip_pixels=8, resolution_m=0.3, stride=4, checkpoint_sha256="c", taxonomy_version="t",
        taxonomy={"genera": [], "species": []}, predict=lambda image, chip_id: [],
    )
    lat, lon = 42.35, -71.06
    from pyproj import Transformer

    x, y = Transformer.from_crs("EPSG:4326", "EPSG:26919", always_xy=True).transform(lon, lat)
    west, north = grid_tile_origin(x, y, 8 * 0.3)
    chip_id = point_chip_id(26919, west, north)
    for year in (2021, 2023):
        _cache_tile(tmp_path, year, chip_id, west, north)
    kwargs = dict(
        artifacts=tmp_path, run="run", year=None, stac_url="unused", city="USBOS", envelopes={},
        territory={}, min_score=0.1, threshold=0.35, coefficients=None, readers=None,
    )

    result = point_predict.predict_tile(predictor, lat, lon, epsg_hint=26919, **kwargs)
    assert result["bundle"]["chipId"] == chip_id
    assert result["bundle"]["imageryVersion"].startswith("naip2023-")  # the newest cached year

    with pytest.raises(AssertionError, match="searched STAC"):
        point_predict.predict_tile(predictor, lat, lon, **kwargs)  # no hint: always asks the catalog

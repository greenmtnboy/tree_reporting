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


def _source(tmp_path, name, west, north, width, height, value, *, nodata=None, masked=False):
    import rasterio
    from rasterio.enums import ColorInterp
    from rasterio.transform import from_origin

    path = tmp_path / f"{name}.tif"
    with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True), rasterio.open(
        path, "w", driver="GTiff", width=width, height=height, count=4,
        dtype="uint8", crs="EPSG:26919", transform=from_origin(west, north, 1, 1), nodata=nodata,
    ) as ds:
        ds.colorinterp = (ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.undefined)
        data = np.full((4, height, width), value, dtype="uint8")
        data[:, 1, 1] = 0  # Valid black unless the source declares it nodata.
        ds.write(data)
        if masked:
            mask = np.full((height, width), 255, dtype="uint8")
            mask[2, 2] = 0
            ds.write_mask(mask)
    return SimpleNamespace(id=name, assets={"image": SimpleNamespace(href=str(path))})


@pytest.mark.parametrize("axis", ["vertical", "horizontal"])
@pytest.mark.parametrize("nodata", [None, 0])
def test_source_seams_use_coverage_not_zero_pixels(tmp_path, axis, nodata):
    width, height = (4, 8) if axis == "vertical" else (8, 4)
    origins = [(0, 8), (4, 8)] if axis == "vertical" else [(0, 4), (0, 8)]
    items = [_source(tmp_path, str(i), *origin, width, height, value, nodata=nodata)
             for i, (origin, value) in enumerate(zip(origins, [100, 200]))]
    image, valid, used = point_predict.read_tile(
        items, crs="EPSG:26919", west=0, north=8, pixels=8, resolution_m=1, bands=[1, 2, 3, 4],
    )
    assert used == ["0", "1"]
    assert (image[0] == 100).sum() == 31
    assert (image[0] == 200).sum() == 31
    assert valid.sum() == (64 if nodata is None else 62)
    assert (valid & np.all(image == 0, axis=0)).sum() == (2 if nodata is None else 0)


def test_missing_neighbour_stays_invalid_and_source_mask_is_respected(tmp_path):
    item = _source(tmp_path, "partial", 0, 8, 4, 8, 100, masked=True)
    image, valid, used = point_predict.read_tile(
        [item], crs="EPSG:26919", west=0, north=8, pixels=8, resolution_m=1, bands=[1, 2, 3, 4],
    )
    assert valid.sum() == 31
    assert not valid[:, 4:].any()
    assert not valid[2, 2]
    assert valid[1, 1]  # Zero values are not the validity policy.
    assert used == ["partial"]


def test_cached_nir_is_not_alpha_and_mask_survives_round_trip(tmp_path):
    import rasterio
    from rasterio.enums import ColorInterp, MaskFlags

    image = np.zeros((4, 8, 8), dtype="uint8")
    image[3] = 17
    valid = np.ones((8, 8), dtype=bool)
    valid[:, 6:] = False
    path = tmp_path / "cache.tif"
    point_predict.write_cached_tile(path, image, crs="EPSG:26919", west=0, north=8,
                                    resolution_m=1, valid=valid)
    with rasterio.open(path) as ds:
        assert ds.colorinterp[3] == ColorInterp.undefined
        assert all(MaskFlags.per_dataset in flags for flags in ds.mask_flag_enums)
        assert np.array_equal(ds.read(), image)
        assert np.array_equal(ds.dataset_mask() > 0, valid)
    assert not path.with_suffix(".tif.msk").exists()
    assert not list(tmp_path.glob("*.tmp.tif"))


def test_legacy_cache_is_not_a_context_neighbour(tmp_path, monkeypatch):
    import rasterio
    from rasterio.transform import from_origin

    legacy = tmp_path / "point-tiles" / "naip2023"
    legacy.mkdir(parents=True)
    name = point_chip_id(26919, 0, 8) + "_100cm"
    with rasterio.open(legacy / f"{name}.tif", "w", driver="GTiff", count=4,
                       width=8, height=8, dtype="uint8", crs="EPSG:26919",
                       transform=from_origin(0, 8, 1, 1)) as ds:
        ds.write(np.full((4, 8, 8), 99, dtype="uint8"))
    (legacy / f"{name}.manifest.json").write_text('{"valid_fraction":1.0}')
    monkeypatch.setattr(point_predict, "year_items", lambda *args: [])
    image = point_predict.neighbour_tile(point_predict.point_cache_root(tmp_path) / "naip2023",
        crs="EPSG:26919", west=0, north=8, pixels=8, resolution_m=1,
        bands=[1, 2, 3, 4], stac_url="unused")
    assert not image.any()
    assert (legacy / f"{name}.tif").exists()  # No destructive migration.


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


def _cache_tile(artifacts, year, chip_id, west, north, value=0, pixels=8):
    stem = point_predict.point_cache_root(artifacts) / f"naip{year}" / f"{chip_id}_30cm"
    point_predict.write_cached_tile(
        stem.with_suffix(".tif"), np.full((4, pixels, pixels), value, dtype=np.uint8),
        crs="EPSG:26919", west=west, north=north, resolution_m=0.3,
    )
    stem.with_name(stem.name + ".manifest.json").write_text(json.dumps({
        "collection": "naip", "year": str(year), "valid_fraction": 1.0,
        "cache_version": point_predict.IMAGERY_CACHE_VERSION,
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


def test_cached_coverage_not_manifest_claim_controls_prediction_gate(tmp_path):
    from pyproj import Transformer

    lat, lon = 42.35, -71.06
    x, y = Transformer.from_crs("EPSG:4326", "EPSG:26919", always_xy=True).transform(lon, lat)
    west, north = grid_tile_origin(x, y, 2.4)
    chip = point_chip_id(26919, west, north)
    _cache_tile(tmp_path, 2023, chip, west, north, value=50)
    path = point_predict.point_cache_root(tmp_path) / "naip2023" / f"{chip}_30cm.tif"
    mask = np.ones((8, 8), dtype=bool)
    mask[:, 4:] = False
    point_predict.write_cached_tile(path, np.full((4, 8, 8), 50, dtype="uint8"),
        crs="EPSG:26919", west=west, north=north, resolution_m=.3, valid=mask)
    # The manifest deliberately still claims 100% coverage. No model needed:
    # the persisted mask must reject the tile before any inference.
    predictor = SimpleNamespace(chip_pixels=8, resolution_m=.3)
    result = point_predict.predict_tile(predictor, lat, lon, artifacts=tmp_path,
        run="unused", year=2023, stac_url="unused", city="USBOS", envelopes={}, territory={},
        min_score=.1, threshold=.35, coefficients=None, readers=None, epsg_hint=26919)
    assert result is None


def test_window_cores_reassemble_exactly_the_tile():
    torch = pytest.importorskip("torch")
    pixels, stride = 16, 2
    surround = np.arange(4 * 24 * 24, dtype=np.int64).reshape(4, 24, 24)  # the tile is [4:20, 4:20]
    windows = torch.from_numpy(point_predict.context_windows(surround, pixels))
    assert windows.shape == (4, 4, pixels, pixels)
    output = {"center_logits": windows[:, 0, ::stride, ::stride], "genus_logits": windows[:, :2, ::stride, ::stride]}

    stitched = point_predict.stitch_cores(output, pixels // stride)

    tile = torch.from_numpy(surround[:, 4:20:stride, 4:20:stride])
    assert torch.equal(stitched["center_logits"][0], tile[0])
    assert torch.equal(stitched["genus_logits"][0], tile[:2])

    ringed = point_predict.stitch_cores(output, pixels // stride, ring=1)  # one neighbour cell each side
    tile = torch.from_numpy(surround[:, 2:22:stride, 2:22:stride])
    assert torch.equal(ringed["center_logits"][0], tile[0])
    assert torch.equal(ringed["genus_logits"][0], tile[:2])


def test_context_image_surrounds_the_tile_with_its_cached_neighbours(tmp_path, monkeypatch):
    monkeypatch.setattr(point_predict, "year_items", lambda bounds, year, stac_url: [])  # nothing to fetch
    west, north, step = 1000.0, 5000.0, 8 * 0.3
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if (dx, dy) != (1, 1):  # the south-east neighbour is past NAIP's edge
                value = 10 * (dy + 2) + dx + 2
                chip = point_chip_id(26919, west + dx * step, north - dy * step)
                _cache_tile(tmp_path, 2023, chip, west + dx * step, north - dy * step, value=value)
    tile = np.full((4, 8, 8), 22, dtype=np.uint8)

    surround = point_predict.context_image(
        tile, point_predict.point_cache_root(tmp_path) / "naip2023", crs="EPSG:26919", west=west, north=north,
        pixels=8, resolution_m=0.3, bands=[1, 2, 3, 4], stac_url="unused",
    )

    assert surround.shape == (4, 12, 12)
    assert (surround[:, 2:10, 2:10] == 22).all()
    assert surround[0, 0, 0] == 11 and surround[0, 0, 5] == 12 and surround[0, 0, 11] == 13  # north row
    assert surround[0, 5, 0] == 21 and surround[0, 5, 11] == 23
    assert surround[0, 11, 0] == 31 and surround[0, 11, 5] == 32 and surround[0, 11, 11] == 0  # no imagery


def test_a_tile_in_context_is_predicted_from_its_surround(tmp_path):
    seen = {}

    def in_context(image, chip_id):
        seen["shape"] = image.shape
        return [{"score": 0.5, "output_x": 1, "output_y": 1, "chip_id": chip_id}]

    predictor = SimpleNamespace(
        chip_pixels=8, resolution_m=0.3, stride=2, checkpoint_sha256="c", taxonomy_version="t", bands=[1, 2, 3, 4],
        taxonomy={"genera": [], "species": []}, predict=lambda image, chip_id: [], predict_in_context=in_context,
    )
    lat, lon = 42.35, -71.06
    from pyproj import Transformer

    x, y = Transformer.from_crs("EPSG:4326", "EPSG:26919", always_xy=True).transform(lon, lat)
    west, north = grid_tile_origin(x, y, 8 * 0.3)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            w, n = west + dx * 2.4, north - dy * 2.4
            _cache_tile(tmp_path, 2023, point_chip_id(26919, w, n), w, n)

    result = point_predict.predict_tile(
        predictor, lat, lon, artifacts=tmp_path, run="run", year=None, stac_url="unused", city="USBOS",
        envelopes={}, territory={}, min_score=0.1, threshold=0.35, coefficients=None, readers=None,
        epsg_hint=26919, context=True,
    )
    assert seen["shape"] == (4, 12, 12)
    assert len(result["bundle"]["predictionLayer"]["predictions"]) == 1

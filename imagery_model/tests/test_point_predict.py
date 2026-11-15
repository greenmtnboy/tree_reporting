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


def test_cores_of_the_kept_region_alone_stitch_to_the_same_tile():
    torch = pytest.importorskip("torch")
    cells = 16
    torch.manual_seed(0)
    output = {"center_logits": torch.randn(4, cells, cells), "genus_logits": torch.randn(4, 3, cells, cells)}
    for ring in (0, 1, 2):
        start, size = point_predict.core_region(cells, ring)
        assert (start, size) == (4 - ring, 8 + 2 * ring)
        region = {key: value[..., start:start + size, start:start + size] for key, value in output.items()}
        whole = point_predict.stitch_cores(output, cells, ring)
        kept = point_predict.stitch_cores(region, cells, ring, origin=start)
        for key in output:
            assert torch.equal(kept[key], whole[key])


def test_a_cached_tile_is_read_once_until_it_is_rewritten(tmp_path, monkeypatch):
    import os
    import rasterio

    chip = point_chip_id(26919, 0, 8)
    _cache_tile(tmp_path, 2023, chip, 0, 8, value=5)
    folder = point_predict.point_cache_root(tmp_path) / "naip2023"
    tif = next(folder.glob("*.tif"))
    manifest = next(folder.glob("*.manifest.json"))
    opened = []
    real_open = rasterio.open
    monkeypatch.setattr(rasterio, "open", lambda *a, **k: opened.append(a[0]) or real_open(*a, **k))

    first = point_predict.cached_tile(tif, manifest)
    again = point_predict.cached_tile(tif, manifest)
    assert len(opened) == 1 and again[0] is first[0]
    assert (first[0] == 5).all() and first[1] == 1.0
    assert not first[0].flags.writeable  # one array, every caller's

    image = np.full(first[0].shape, 9, dtype=np.uint8)
    point_predict.write_cached_tile(tif, image, crs="EPSG:26919", west=0, north=8, resolution_m=0.3)
    stamp = tif.stat().st_mtime_ns + 1_000_000_000
    os.utime(tif, ns=(stamp, stamp))
    assert (point_predict.cached_tile(tif, manifest)[0] == 9).all()


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
    assert result["png"]

    unrendered = point_predict.predict_tile(
        predictor, lat, lon, artifacts=tmp_path, run="run", year=None, stac_url="unused", city="USBOS",
        envelopes={}, territory={}, min_score=0.1, threshold=0.35, coefficients=None, readers=None,
        epsg_hint=26919, context=True, render=False,
    )
    assert unrendered["png"] == b""
    assert unrendered["bundle"]["predictionLayer"] == result["bundle"]["predictionLayer"]


def test_a_thinner_margin_needs_a_block_of_tiles_for_its_cores_to_fit():
    assert point_predict.block_tiles(512, 128) == 1  # a core of half a tile: every tile its own block
    assert point_predict.block_tiles(512, 64) == 3   # 4 cores of 384 px are 3 tiles
    assert point_predict.block_tiles(512, 96) == 5
    assert point_predict.block_tiles(512, 32) == 7
    for margin in (0, 256, 300):
        with pytest.raises(ValueError):
            point_predict.block_tiles(512, margin)


def test_the_cores_of_a_blocks_windows_reassemble_exactly_the_block():
    torch = pytest.importorskip("torch")
    pixels, margin, stride, tiles = 16, 2, 2, 3
    assert point_predict.block_tiles(pixels, margin) == tiles
    side = tiles * pixels + 2 * margin  # the block is [2:50, 2:50]
    surround = np.arange(4 * side * side, dtype=np.int64).reshape(4, side, side)
    windows = torch.from_numpy(point_predict.context_windows(surround, pixels, margin))
    assert windows.shape == (16, 4, pixels, pixels)
    output = {"center_logits": windows[:, 0, ::stride, ::stride], "genus_logits": windows[:, :2, ::stride, ::stride]}
    cells, margin_cells = tiles * pixels // stride, margin // stride

    stitched = point_predict.stitch_cores(output, cells, margin=margin_cells)
    block = torch.from_numpy(surround[:, 2:50:stride, 2:50:stride])
    assert torch.equal(stitched["center_logits"][0], block[0])
    assert torch.equal(stitched["genus_logits"][0], block[:2])

    ringed = point_predict.stitch_cores(output, cells, ring=1, margin=margin_cells)
    block = torch.from_numpy(surround[:, 0:52:stride, 0:52:stride])
    assert torch.equal(ringed["center_logits"][0], block[0])

    start, size = point_predict.core_region(pixels // stride, 1, margin_cells)
    assert (start, size) == (0, 8)
    region = {key: value[..., start:start + size, start:start + size] for key, value in output.items()}
    kept = point_predict.stitch_cores(region, cells, ring=1, origin=start, margin=margin_cells)
    assert torch.equal(kept["genus_logits"], ringed["genus_logits"])


def test_only_the_windows_a_wanted_tile_touches_are_needed():
    needed = lambda tiles: point_predict.windows_needed(4, 384, 512, tiles, 4)  # noqa: E731
    assert needed([(0, 0)]) == [0, 1, 4, 5]
    assert needed([(1, 1)]) == [5, 6, 9, 10]
    assert needed([(2, 0)]) == [2, 3, 6, 7]
    assert needed([(c, r) for c in range(3) for r in range(3)]) == list(range(16))
    assert needed([]) == []


def _block_predictor(seen):
    torch = pytest.importorskip("torch")

    def forward(windows, region=None):
        seen.append(len(windows))
        start, size = region
        centre = torch.from_numpy(windows[:, 0, ::2, ::2].astype("float32")) / 10 - 5
        centre = centre[:, start:start + size, start:start + size]
        k = len(windows)
        return {
            "center_logits": centre, "dbh_log1p": torch.zeros_like(centre),
            "genus_logits": torch.zeros(k, 2, size, size), "species_logits": torch.zeros(k, 3, size, size),
        }

    predictor = SimpleNamespace(
        chip_pixels=16, stride=2, _forward=forward,
        config={"evaluation": {"nms_kernel": 3, "max_detections_per_chip": 512}},
    )
    predictor._decode = lambda *a, **k: point_predict.Predictor._decode(predictor, *a, **k)
    return predictor


def test_a_block_is_decoded_once_and_its_peaks_go_to_their_tiles():
    seen = []
    predictor = _block_predictor(seen)
    margin, side = 2, 3 * 16 + 4
    image = np.zeros((4, side, side), dtype=np.uint8)

    def crown(cell_x, cell_y):  # block output cells; the block starts *margin* pixels in
        image[:, 2 * cell_y + margin, 2 * cell_x + margin] = 100

    crown(3, 4)     # tile (0, 0)
    crown(21, 10)   # tile (2, 1), local (5, 2)
    crown(8, 20)    # first column of tile (1, 2)
    crown(-1, 6)    # in the ring west of the block: the neighbour block's
    chips = {(c, r): f"chip-{c}-{r}" for c in range(3) for r in range(3)}

    records = point_predict.Predictor.predict_block(predictor, image, margin, chips, 0.1)

    assert sum(seen) == 16
    found = {position: [(r["chip_id"], r["output_x"], r["output_y"]) for r in rows]
             for position, rows in records.items() if rows}
    assert found == {
        (0, 0): [("chip-0-0", 3, 4)], (2, 1): [("chip-2-1", 5, 2)], (1, 2): [("chip-1-2", 0, 4)],
    }
    assert all(r["score"] > 0.9 for rows in records.values() for r in rows)

    seen.clear()
    one = point_predict.Predictor.predict_block(predictor, image, margin, {(2, 1): "chip-2-1"}, 0.1)
    assert sum(seen) == 4  # the four windows around the tile, not sixteen
    assert [(r["output_x"], r["output_y"]) for r in one[(2, 1)]] == [(5, 2)]


def test_a_block_predicts_its_wanted_tiles_from_shared_windows(tmp_path):
    asked = {}

    def predict_block(image, margin, chip_ids, min_score):
        asked.update(shape=image.shape, margin=margin, chips=dict(chip_ids))
        return {position: [{"score": 0.5, "output_x": 1, "output_y": 1, "chip_id": chip}]
                for position, chip in chip_ids.items()}

    predictor = SimpleNamespace(
        chip_pixels=8, resolution_m=0.3, stride=2, checkpoint_sha256="c", taxonomy_version="t", bands=[1, 2, 3, 4],
        taxonomy={"genera": [], "species": []}, predict_block=predict_block,
    )
    from pyproj import Transformer

    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:26919", always_xy=True)
    back = Transformer.from_crs("EPSG:26919", "EPSG:4326", always_xy=True)
    x, y = to_utm.transform(-71.06, 42.35)
    west, north = grid_tile_origin(x, y, 2.4)  # the block's north-west tile
    for row in range(-1, 4):
        for column in range(-1, 4):
            w, n = west + column * 2.4, north - row * 2.4
            _cache_tile(tmp_path, 2023, point_chip_id(26919, w, n), w, n, value=10 * (row + 1) + column + 1)

    def centre(column, row):
        lon, lat = back.transform(west + (column + 0.5) * 2.4, north - (row + 0.5) * 2.4)
        return lat, lon

    wanted = {(0, 0): centre(0, 0), (2, 1): centre(2, 1)}
    results = point_predict.predict_block(
        predictor, wanted, tiles=3, margin=2, artifacts=tmp_path, run="run", year=None, stac_url="unused",
        city="USBOS", envelopes={}, territory={}, min_score=0.1, threshold=0.35, coefficients=None, readers=None,
        epsg_hint=26919, render=False,
    )

    assert asked["shape"] == (4, 28, 28) and asked["margin"] == 2
    assert asked["chips"] == {(0, 0): point_chip_id(26919, west, north),
                              (2, 1): point_chip_id(26919, west + 4.8, north - 2.4)}
    assert set(results) == set(wanted)
    for position, result in results.items():
        assert result["bundle"]["chipId"] == asked["chips"][position]
        assert len(result["bundle"]["predictionLayer"]["predictions"]) == 1


def test_a_block_image_is_the_block_and_a_margin_of_its_neighbours(tmp_path, monkeypatch):
    monkeypatch.setattr(point_predict, "year_items", lambda bounds, year, stac_url: [])
    west, north, step = 1000.0, 5000.0, 8 * 0.3
    for row in range(-1, 3):
        for column in range(-1, 3):
            if (column, row) != (2, 2):  # past NAIP's edge
                w, n = west + column * step, north - row * step
                _cache_tile(tmp_path, 2023, point_chip_id(26919, w, n), w, n, value=10 * (row + 2) + column + 2)
    own = np.full((4, 8, 8), 99, dtype=np.uint8)

    image = point_predict.block_image(
        {(0, 0): own}, point_predict.point_cache_root(tmp_path) / "naip2023", crs="EPSG:26919", west=west,
        north=north, tiles=2, pixels=8, margin=2, resolution_m=0.3, bands=[1, 2, 3, 4], stac_url="unused",
    )

    assert image.shape == (4, 20, 20)
    assert (image[:, 2:10, 2:10] == 99).all()          # the tile in hand
    assert (image[:, 2:10, 10:18] == 23).all()         # its east neighbour, read from the cache
    assert (image[:, 10:18, 10:18] == 33).all()
    assert image[0, 0, 0] == 11 and image[0, 0, 19] == 14 and image[0, 19, 0] == 41
    assert image[0, 19, 19] == 0                       # no imagery there

import io
import json
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from rasterio.windows import Window

from urban_tree_ml.chips import build_chips
from urban_tree_ml.config import load_config
from urban_tree_ml.prepared_image_cache import PreparedImageCache, raster_identity


class MemoryStore:
    def __init__(self):
        self.objects = {}

    def download(self, key, destination):
        if key not in self.objects:
            return False
        destination.write_bytes(self.objects[key])
        return True

    def upload(self, key, source):
        self.objects.setdefault(key, source.read_bytes())


def write_raster(path, value=50):
    pixels = np.full((4, 32, 32), value, dtype=np.uint8)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=32,
        height=32,
        count=4,
        dtype="uint8",
        crs="EPSG:32610",
        transform=from_origin(550000, 4185000, 0.6, 0.6),
    ) as target:
        target.write(pixels)


def read_cache(
    path, local, store, bands=(1, 2, 3, 4), scale=1 / 255, window=None, mask_recipe="test-v1"
):
    window = Window(0, 0, 32, 32) if window is None else window
    with rasterio.open(path) as source:
        cache = PreparedImageCache(source, bands, scale, local, store, mask_recipe)
        prepared = cache.read(
            window, lambda: np.all(source.read_masks(bands, window=window) > 0, axis=0)
        )
    return prepared, cache.stats


def test_remote_and_local_hits_reuse_identical_compressed_image(tmp_path):
    source = tmp_path / "source.tif"
    write_raster(source)
    store = MemoryStore()
    first, stats = read_cache(source, tmp_path / "first", store)
    assert stats["misses"] == 1
    remote, stats = read_cache(source, tmp_path / "second", store)
    assert stats["remote_hits"] == 1
    local, stats = read_cache(source, tmp_path / "second", store)
    assert stats["local_hits"] == 1
    assert first.archive == remote.archive == local.archive
    output = io.BytesIO()
    targets = {"center": np.ones((16, 16), np.float32)}
    remote.write_targets(output, targets)
    with np.load(io.BytesIO(output.getvalue()), allow_pickle=False) as arrays:
        assert set(arrays.files) == {"image", "center"}
        np.testing.assert_array_equal(arrays["image"], first.image)
        np.testing.assert_array_equal(arrays["center"], targets["center"])
    # Appending targets preserves the original local file header and compressed
    # image stream. Only the trailing ZIP central directory is rewritten.
    with zipfile.ZipFile(io.BytesIO(first.archive)) as archive:
        image_end = archive.start_dir
    assert output.getvalue()[:image_end] == first.archive[:image_end]


@pytest.mark.parametrize("change", ["pixels", "bands", "scale", "window", "mask_recipe", "sidecar"])
def test_pixel_recipe_changes_miss_cache(tmp_path, change):
    source = tmp_path / "source.tif"
    write_raster(source)
    store = MemoryStore()
    read_cache(source, tmp_path / "cache", store)
    kwargs = {}
    if change == "pixels":
        write_raster(source, value=100)
    elif change == "bands":
        kwargs["bands"] = (4, 3, 2, 1)
    elif change == "scale":
        kwargs["scale"] = 1 / 100
    elif change == "window":
        kwargs["window"] = Window(0, 0, 16, 16)
    elif change == "mask_recipe":
        kwargs["mask_recipe"] = "test-v2"
    else:
        Path(str(source) + ".aux.xml").write_text("<PAMDataset><Metadata/></PAMDataset>")
    _, stats = read_cache(source, tmp_path / "cache", store, **kwargs)
    assert stats["misses"] == 1
    assert len(store.objects) == 2


def test_vrt_identity_tracks_dependencies_and_survives_relocation(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    source = first / "source.tif"
    write_raster(source)
    vrt = first / "mosaic.vrt"
    vrt.write_text(
        "<VRTDataset><VRTRasterBand><SimpleSource>"
        '<SourceFilename relativeToVRT="1">source.tif</SourceFilename>'
        "</SimpleSource></VRTRasterBand></VRTDataset>"
    )
    identity = raster_identity(vrt)
    shutil.copytree(first, second)
    assert raster_identity(second / "mosaic.vrt") == identity
    write_raster(second / "source.tif", value=100)
    assert raster_identity(second / "mosaic.vrt") != identity


def test_corrupt_local_cache_recovers_and_corrupt_remote_fails(tmp_path):
    source = tmp_path / "source.tif"
    write_raster(source)
    store = MemoryStore()
    read_cache(source, tmp_path / "cache", store)
    local = next((tmp_path / "cache").rglob("*.zip"))
    local.write_bytes(b"incomplete")
    _, stats = read_cache(source, tmp_path / "cache", store)
    assert stats["remote_hits"] == stats["repaired_local_entries"] == 1
    key = next(iter(store.objects))
    store.objects[key] = b"incomplete"
    with pytest.raises(zipfile.BadZipFile):
        read_cache(source, tmp_path / "empty", store)
    assert not list((tmp_path / "empty").rglob("*.zip"))
    assert not list((tmp_path / "empty").rglob("*.part"))


def test_concurrent_producers_leave_readable_complete_local_entries(tmp_path):
    source = tmp_path / "source.tif"
    write_raster(source)
    store = MemoryStore()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(read_cache, source, tmp_path / "cache", store) for _ in range(2)]
        results = [future.result()[0] for future in futures]
    np.testing.assert_array_equal(results[0].image, results[1].image)
    _, stats = read_cache(source, tmp_path / "cache", store)
    assert stats["local_hits"] == 1
    assert not list((tmp_path / "cache").rglob("*.part"))


def test_build_chips_reuses_pixels_but_rebuilds_changed_curation(tmp_path, monkeypatch):
    from urban_tree_ml import prepared_image_cache

    monkeypatch.setenv("TREE_ML_PREPARED_IMAGE_CACHE_URI", "")
    config = load_config(Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml")
    config.paths.root = tmp_path
    raster = tmp_path / "feedback-image.tif"
    origin_x, origin_y = 550000, 4185000
    inverse = Transformer.from_crs("EPSG:32610", "EPSG:4326", always_xy=True)
    longitude, latitude = inverse.transform(origin_x + 128 * 0.6, origin_y - 128 * 0.6)
    inventory = tmp_path / "inventory/ussfo"
    inventory.mkdir(parents=True)
    pd.DataFrame(
        [
            dict(
                tree_id="tree",
                longitude=longitude,
                latitude=latitude,
                split_eligible=True,
                split="train",
                dbh_log1p=2.0,
                genus_id=1,
                species_id=2,
                dbh_eligible=True,
                genus_eligible=True,
                species_eligible=True,
            )
        ]
    ).to_parquet(inventory / "inventory.parquet", index=False)
    frame = pd.read_parquet(inventory / "inventory.parquet")
    retained = frame.assign(tree_id="retained", longitude=longitude + 0.0002)
    pd.concat([frame, retained], ignore_index=True).to_parquet(
        inventory / "inventory.parquet", index=False
    )
    pixels = np.full((4, 256, 256), 50, dtype=np.uint8)
    pixels[3] = 10
    with rasterio.open(
        raster,
        "w",
        driver="GTiff",
        width=256,
        height=256,
        count=4,
        dtype="uint8",
        crs="EPSG:32610",
        transform=from_origin(origin_x, origin_y, 0.6, 0.6),
    ) as output:
        output.write(pixels)
    feedback_path = tmp_path / "qa/registration/feedback-image/training-feedback.json"
    feedback_path.parent.mkdir(parents=True)
    feedback = dict(
        schema_version=1,
        dataset=config.dataset,
        source_raster_name=raster.name,
        registration={"east_m": 0.0, "north_m": 0.0},
        region_overrides=[],
        exclusions=[{"tree_id": "tree", "split": "train", "reason": "not-tree"}],
    )
    feedback_path.write_text(json.dumps(feedback))

    def build(name):
        summary = build_chips(config, raster, output_dataset=name)
        manifest = pd.read_parquet(summary["manifest"])
        path = Path(summary["manifest"]).parent / manifest.iloc[0]["path"]
        with np.load(path, allow_pickle=False) as arrays:
            values = {key: arrays[key] for key in arrays.files}
        return summary, values

    _, baseline = build("uncached")
    store = MemoryStore()
    monkeypatch.setattr(prepared_image_cache, "GCSStore", lambda uri: store)
    monkeypatch.setenv("TREE_ML_PREPARED_IMAGE_CACHE_URI", "gs://test/images")
    cold_summary, cold = build("cold")
    assert cold_summary["prepared_image_cache"]["misses"] == 1
    assert baseline.keys() == cold.keys()
    for name in baseline:
        np.testing.assert_array_equal(baseline[name], cold[name])

    feedback["exclusions"] = []
    feedback_path.write_text(json.dumps(feedback))
    warm_summary, warm = build("warm-edited")
    assert warm_summary["prepared_image_cache"]["local_hits"] == 1
    np.testing.assert_array_equal(cold["image"], warm["image"])
    assert not np.array_equal(cold["center"], warm["center"])
    assert not np.array_equal(cold["detection_mask"], warm["detection_mask"])
    monkeypatch.setenv("TREE_ML_PREPARED_IMAGE_CACHE_URI", "")
    _, edited_baseline = build("uncached-edited")
    for name in warm:
        np.testing.assert_array_equal(warm[name], edited_baseline[name])


def test_source_change_during_pixel_read_cannot_publish_stale_key(tmp_path):
    path = tmp_path / "source.tif"
    write_raster(path)
    store = MemoryStore()
    with rasterio.open(path) as source:
        cache = PreparedImageCache(
            source, (1, 2, 3, 4), 1 / 255, tmp_path / "cache", store, "test-v1"
        )

        def changed_mask():
            Path(str(path) + ".aux.xml").write_text("<PAMDataset/>")
            return np.ones((32, 32), dtype=bool)

        with pytest.raises(RuntimeError, match="Imagery changed"):
            cache.read(Window(0, 0, 32, 32), changed_mask)
    assert store.objects == {}
    assert not list((tmp_path / "cache").rglob("*.part"))


def test_config_file_environment_override_and_disable(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from urban_tree_ml import prepared_image_cache

    source_path = tmp_path / "source.tif"
    write_raster(source_path)
    settings = tmp_path / "prepared-image-cache.json"
    settings.write_text(json.dumps({"uri": "gs://fixture/config"}))
    seen = []

    def store(uri):
        seen.append(uri)
        return MemoryStore()

    monkeypatch.setattr(prepared_image_cache, "GCSStore", store)
    monkeypatch.delenv("TREE_ML_PREPARED_IMAGE_CACHE_URI", raising=False)
    imagery = SimpleNamespace(bands=[1, 2, 3, 4], input_scale=1 / 255)
    with rasterio.open(source_path) as source:
        assert PreparedImageCache.configured(source, imagery, tmp_path, "v1") is not None
        monkeypatch.setenv("TREE_ML_PREPARED_IMAGE_CACHE_URI", "gs://fixture/environment")
        assert PreparedImageCache.configured(source, imagery, tmp_path, "v1") is not None
        monkeypatch.setenv("TREE_ML_PREPARED_IMAGE_CACHE_URI", "")
        assert PreparedImageCache.configured(source, imagery, tmp_path, "v1") is None
    assert seen == ["gs://fixture/config", "gs://fixture/environment"]


@pytest.mark.parametrize("conflict", [False, True])
def test_gcs_upload_race_verifies_winning_pixels(tmp_path, conflict):
    from google.api_core.exceptions import PreconditionFailed

    from urban_tree_ml.prepared_image_cache import GCSStore

    raster = tmp_path / "source.tif"
    write_raster(raster)
    memory = MemoryStore()
    read_cache(raster, tmp_path / "local", memory)
    key = next(iter(memory.objects))
    proposed = next((tmp_path / "local").rglob("*.zip"))
    winner = memory.objects[key]
    if conflict:
        # Preserve a valid manifest but associate different pixels with the
        # proposed key, simulating an inconsistent concurrent producer.
        with zipfile.ZipFile(io.BytesIO(winner)) as archive:
            metadata = json.loads(archive.read("manifest.json"))
            mask_bytes = archive.read("valid-mask.npy")
        from urban_tree_ml.prepared_image_cache import digest

        image = io.BytesIO()
        np.savez_compressed(image, image=np.ones((4, 32, 32), dtype=np.float32))
        metadata["image_sha256"] = digest(image.getvalue())
        changed = io.BytesIO()
        with zipfile.ZipFile(changed, "w") as archive:
            archive.writestr("manifest.json", json.dumps(metadata))
            archive.writestr("image.npz", image.getvalue())
            archive.writestr("valid-mask.npy", mask_bytes)
        winner = changed.getvalue()

    class Blob:
        def upload_from_filename(self, filename, **kwargs):
            assert kwargs["if_generation_match"] == 0
            raise PreconditionFailed("another writer won")

        def download_to_filename(self, filename, **kwargs):
            Path(filename).write_bytes(winner)

    store = object.__new__(GCSStore)
    store.blob = lambda unused: Blob()
    if conflict:
        with pytest.raises(ValueError, match="Conflicting prepared pixels"):
            store.upload(key, proposed)
    else:
        store.upload(key, proposed)
    assert not list(proposed.parent.glob("*.compare"))

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from urban_tree_ml.config import load_config
from urban_tree_ml.imagery import build_vrt_mosaic, fetch_stac_item


def _write_test_raster(path: Path, resolution: float = 0.6) -> None:
    image = np.full((4, 32, 32), 50, dtype=np.uint8)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=32,
        height=32,
        count=4,
        dtype="uint8",
        crs="EPSG:32610",
        transform=from_origin(550_000, 4_185_000, resolution, resolution),
    ) as target:
        target.write(image)


def _write_index(root: Path, source: Path, item_id: str) -> None:
    index_dir = root / "imagery" / "ussfo"
    index_dir.mkdir(parents=True)
    (index_dir / "stac-items.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": item_id,
                        "collection": "naip",
                        "datetime": "2022-05-18T16:00:00+00:00",
                        "asset_key": "image",
                        "asset_href": source.as_uri(),
                        "band_names": ["red", "green", "blue", "nir"],
                        "properties": {"gsd": 0.6, "naip:year": "2022"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_fetch_stac_item_downloads_validates_and_records_unsigned_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml")
    config.paths.root = tmp_path / "artifacts"
    source = tmp_path / "source.tif"
    _write_test_raster(source)
    item_id = "ca_test_20220518"
    _write_index(config.paths.root, source, item_id)
    monkeypatch.setattr("urban_tree_ml.imagery._sign_asset_href", lambda href: href)

    result = fetch_stac_item(config, item_id)

    destination = Path(result["path"])
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert result["status"] == "downloaded"
    assert destination == config.paths.root / "imagery" / "ussfo" / "2022" / f"{item_id}.tif"
    assert destination.exists()
    assert manifest["source_href"] == source.as_uri()
    assert manifest["raster"]["selected_band_names"] == ["red", "green", "blue", "nir"]
    assert "sig=" not in Path(result["manifest"]).read_text(encoding="utf-8")
    assert fetch_stac_item(config, item_id)["status"] == "existing"


def test_fetch_stac_item_does_not_publish_a_raster_that_fails_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml")
    config.paths.root = tmp_path / "artifacts"
    source = tmp_path / "wrong-resolution.tif"
    _write_test_raster(source, resolution=1.0)
    item_id = "ca_bad_20220518"
    _write_index(config.paths.root, source, item_id)
    monkeypatch.setattr("urban_tree_ml.imagery._sign_asset_href", lambda href: href)

    with pytest.raises(ValueError, match="resolution"):
        fetch_stac_item(config, item_id)

    destination = config.paths.root / "imagery" / "ussfo" / "2022" / f"{item_id}.tif"
    assert not destination.exists()
    assert not list(destination.parent.glob("*.part"))


def test_build_vrt_mosaic_preserves_bands_geometry_and_provenance(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml")
    config.paths.root = tmp_path / "artifacts"
    imagery_dir = config.paths.root / "imagery" / "ussfo" / "2022"
    imagery_dir.mkdir(parents=True)
    west = imagery_dir / "west.tif"
    east = imagery_dir / "east.tif"
    _write_test_raster(west)
    image = np.full((4, 32, 32), 100, dtype=np.uint8)
    with rasterio.open(
        east,
        "w",
        driver="GTiff",
        width=32,
        height=32,
        count=4,
        dtype="uint8",
        crs="EPSG:32610",
        transform=from_origin(550_000 + 32 * 0.6, 4_185_000, 0.6, 0.6),
    ) as target:
        target.write(image)
    for path, item_id in ((west, "west-item"), (east, "east-item")):
        path.with_suffix(".manifest.json").write_text(
            json.dumps({"item_id": item_id, "sha256": f"sha-{item_id}"}),
            encoding="utf-8",
        )

    result = build_vrt_mosaic(config, "2022")

    mosaic_path = Path(result["path"])
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    with rasterio.open(mosaic_path) as mosaic:
        assert (mosaic.count, mosaic.width, mosaic.height) == (4, 64, 32)
        assert np.all(mosaic.read(1, window=((0, 32), (0, 32))) == 50)
        assert np.all(mosaic.read(1, window=((0, 32), (32, 64))) == 100)
    assert result["status"] == "created"
    assert result["sources"] == 2
    assert [source["item_id"] for source in manifest["sources"]] == [
        "east-item",
        "west-item",
    ]
    assert build_vrt_mosaic(config, "2022")["status"] == "existing"


def test_build_vrt_mosaic_rejects_sources_on_different_pixel_grids(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml")
    config.paths.root = tmp_path / "artifacts"
    imagery_dir = config.paths.root / "imagery" / "ussfo" / "2022"
    imagery_dir.mkdir(parents=True)
    _write_test_raster(imagery_dir / "aligned.tif")
    shifted = imagery_dir / "shifted.tif"
    image = np.full((4, 32, 32), 50, dtype=np.uint8)
    with rasterio.open(
        shifted,
        "w",
        driver="GTiff",
        width=32,
        height=32,
        count=4,
        dtype="uint8",
        crs="EPSG:32610",
        transform=from_origin(550_000.3, 4_185_000, 0.6, 0.6),
    ) as target:
        target.write(image)

    with pytest.raises(ValueError, match="common pixel grid"):
        build_vrt_mosaic(config, "2022")

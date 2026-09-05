import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin

from urban_tree_ml.config import load_config
from urban_tree_ml.imagery import (
    build_vrt_mosaic,
    fetch_configured_stac_items,
    fetch_stac_item,
    index_stac_coverage,
)


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


def test_index_stac_coverage_ranks_items_by_inventory_density(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml")
    config.paths.root = tmp_path / "artifacts"
    inventory_dir = config.paths.root / "inventory" / "ussfo"
    inventory_dir.mkdir(parents=True)
    pd.DataFrame(
        {"longitude": [-71.10, -71.09, -71.00], "latitude": [42.35, 42.36, 42.40]}
    ).to_parquet(inventory_dir / "inventory.parquet", index=False)

    def item(item_id: str, bbox: list[float]) -> SimpleNamespace:
        return SimpleNamespace(
            id=item_id,
            collection_id="naip",
            datetime=datetime(2023, 6, 1, tzinfo=UTC),
            bbox=bbox,
            assets={
                "image": SimpleNamespace(
                    href=f"https://example.test/{item_id}.tif",
                    media_type="image/tiff",
                    extra_fields={},
                )
            },
            properties={"naip:year": 2023, "gsd": 0.6},
        )

    items = [
        item("sparse", [-71.01, 42.39, -70.99, 42.41]),
        item("dense", [-71.11, 42.34, -71.08, 42.37]),
    ]
    search = SimpleNamespace(item_collection=lambda: items)
    catalog = SimpleNamespace(search=lambda **_kwargs: search)
    fake_module = SimpleNamespace(Client=SimpleNamespace(open=lambda _url: catalog))
    monkeypatch.setitem(sys.modules, "pystac_client", fake_module)

    result = index_stac_coverage(config)

    assert result["items_by_year"] == {"2023": 2}
    assert result["top_items_by_inventory_count"] == [
        {"id": "dense", "year": 2023, "inventory_tree_count": 2},
        {"id": "sparse", "year": 2023, "inventory_tree_count": 1},
    ]


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


def test_fetch_configured_stac_items_uses_the_pinned_footprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml")
    config.imagery.item_ids = ["first", "second"]
    calls: list[tuple[str, bool]] = []

    def fetch(_config: object, item_id: str, *, overwrite: bool) -> dict[str, object]:
        calls.append((item_id, overwrite))
        return {"item_id": item_id, "status": "downloaded" if item_id == "first" else "existing"}

    monkeypatch.setattr("urban_tree_ml.imagery.fetch_stac_item", fetch)

    result = fetch_configured_stac_items(config, overwrite=True)

    assert calls == [("first", True), ("second", True)]
    assert result["items"] == 2
    assert result["downloaded"] == 1
    assert result["existing"] == 1


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


def test_build_vrt_mosaic_uses_only_configured_items(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml")
    config.paths.root = tmp_path / "artifacts"
    config.imagery.item_ids = ["west"]
    imagery_dir = config.paths.root / "imagery" / "ussfo" / "2022"
    imagery_dir.mkdir(parents=True)
    _write_test_raster(imagery_dir / "west.tif")
    _write_test_raster(imagery_dir / "unrelated.tif")

    result = build_vrt_mosaic(config, "2022")

    with rasterio.open(result["path"]) as mosaic:
        assert (mosaic.width, mosaic.height) == (32, 32)
    assert result["sources"] == 1
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert [source["item_id"] for source in manifest["sources"]] == ["west"]


def test_build_vrt_mosaic_downsamples_native_resolution_for_model_input(
    tmp_path: Path,
) -> None:
    config = load_config(Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml")
    config.paths.root = tmp_path / "artifacts"
    config.imagery.source_resolution_m = 0.3
    imagery_dir = config.paths.root / "imagery" / "ussfo" / "2023"
    imagery_dir.mkdir(parents=True)
    _write_test_raster(imagery_dir / "native.tif", resolution=0.3)

    result = build_vrt_mosaic(config, "2023")

    with rasterio.open(result["path"]) as mosaic:
        assert mosaic.res == (0.6, 0.6)
        assert (mosaic.width, mosaic.height) == (16, 16)
        assert np.all(mosaic.read(1) == 50)


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

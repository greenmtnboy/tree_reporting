from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

from urban_tree_ml.chips import build_chips
from urban_tree_ml.config import load_config


def test_build_chips_materializes_targets_and_training_statistics(tmp_path: Path) -> None:
    config_path = Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml"
    config = load_config(config_path)
    config.paths.root = tmp_path

    inventory_dir = tmp_path / "inventory" / "ussfo"
    inventory_dir.mkdir(parents=True)
    origin_x, origin_y = 550_000.0, 4_185_000.0
    center_x = origin_x + 128 * 0.6
    center_y = origin_y - 128 * 0.6
    inverse = Transformer.from_crs("EPSG:32610", "EPSG:4326", always_xy=True)
    longitude, latitude = inverse.transform(center_x, center_y)
    pd.DataFrame(
        {
            "longitude": [longitude],
            "latitude": [latitude],
            "split_eligible": [True],
            "split": ["train"],
            "dbh_log1p": [2.0],
            "genus_id": [1],
            "species_id": [2],
        }
    ).to_parquet(inventory_dir / "inventory.parquet", index=False)

    raster_path = tmp_path / "image.tif"
    image = np.full((4, 256, 256), 50, dtype=np.uint8)
    image[3] = 100
    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        width=256,
        height=256,
        count=4,
        dtype="uint8",
        crs="EPSG:32610",
        transform=from_origin(origin_x, origin_y, 0.6, 0.6),
    ) as target:
        target.write(image)

    summary = build_chips(config, raster_path)

    assert summary["chips"] == 1
    manifest = pd.read_parquet(summary["manifest"])
    with np.load(Path(summary["manifest"]).parent / manifest.iloc[0]["path"]) as chip:
        assert chip["image"].shape == (4, 256, 256)
        assert chip["center"].max() == 1
        assert chip["species"].max() == 2

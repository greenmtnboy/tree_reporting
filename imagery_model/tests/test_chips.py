import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

from urban_tree_ml.chips import build_chips
from urban_tree_ml.config import ReferenceConfig, load_config


def test_build_chips_materializes_targets_and_training_statistics(tmp_path: Path) -> None:
    config_path = Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml"
    config = load_config(config_path)
    config.paths.root = tmp_path

    inventory_dir = tmp_path / "inventory" / "ussfo"
    inventory_dir.mkdir(parents=True)
    origin_x, origin_y = 550_000.0, 4_185_000.0
    inverse = Transformer.from_crs("EPSG:32610", "EPSG:4326", always_xy=True)
    projected = [
        (origin_x + 128 * 0.6, origin_y - 128 * 0.6),
        (origin_x + 140 * 0.6, origin_y - 128 * 0.6),
        (origin_x + 160.0 * 0.6, origin_y - 160.0 * 0.6),
        (origin_x + 160.4 * 0.6, origin_y - 160.4 * 0.6),
    ]
    coordinates = [inverse.transform(x, y) for x, y in projected]
    pd.DataFrame(
        {
            "tree_id": [
                "complete-tree",
                "detection-only-tree",
                "collision-a",
                "collision-b",
            ],
            "longitude": [value[0] for value in coordinates],
            "latitude": [value[1] for value in coordinates],
            "split_eligible": [True, True, True, True],
            "split": ["train", "train", "train", "train"],
            "dbh_log1p": [2.0, np.nan, 1.0, 3.0],
            "genus_id": [1, -1, 0, 1],
            "species_id": [2, -1, 0, 2],
            "dbh_eligible": [True, False, True, True],
            "genus_eligible": [True, False, True, True],
            "species_eligible": [True, False, True, True],
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
        assert chip["center"][64, 70] == 1
        assert chip["dbh_mask"][64, 70] == 0
        assert chip["genus_mask"][64, 70] == 0
        assert chip["species_mask"][64, 70] == 0
        assert chip["center"][80, 80] == 0
        assert chip["dbh_mask"][80, 80] == 0
        assert chip["genus_mask"][80, 80] == 0
        assert chip["species_mask"][80, 80] == 0
    assert summary["candidate_trees"] == 4
    assert summary["trees"] == 2
    assert summary["collision_cells"] == 1
    assert summary["collision_excluded_points"] == 2
    collision_exclusions = pd.read_parquet(summary["collision_exclusions"])
    assert set(collision_exclusions["tree_id"]) == {"collision-a", "collision-b"}
    assert set(collision_exclusions["collision_size"]) == {2}
    labels = pd.read_parquet(summary["labels"])
    assert set(labels["tree_id"]) == {"complete-tree", "detection-only-tree"}

    reference_normalization = tmp_path / "sf-normalization.json"
    reference_normalization.write_text(
        json.dumps({"mean": [10, 20, 30, 40], "std": [2, 3, 4, 5]}),
        encoding="utf-8",
    )
    config.dataset = "external-city-fixture"
    config.reference = ReferenceConfig(
        taxonomy_path=tmp_path / "sf-taxonomy.json",
        normalization_path=reference_normalization,
    )

    external_summary = build_chips(config, raster_path)

    applied = json.loads(Path(external_summary["normalization"]).read_text(encoding="utf-8"))
    local = json.loads(
        Path(external_summary["local_normalization"]).read_text(encoding="utf-8")
    )
    assert applied["mean"] == [10.0, 20.0, 30.0, 40.0]
    assert applied["std"] == [2.0, 3.0, 4.0, 5.0]
    assert local["mean"] == pytest.approx([50 / 255, 50 / 255, 50 / 255, 100 / 255])


def test_build_chips_applies_finalized_registration_feedback(tmp_path: Path) -> None:
    config_path = Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml"
    config = load_config(config_path)
    config.paths.root = tmp_path
    inventory_dir = tmp_path / "inventory" / "ussfo"
    inventory_dir.mkdir(parents=True)
    origin_x, origin_y = 550_000.0, 4_185_000.0
    inverse = Transformer.from_crs("EPSG:32610", "EPSG:4326", always_xy=True)
    projected = [
        (origin_x + 128 * 0.6, origin_y - 128 * 0.6),
        (origin_x + 64 * 0.6, origin_y - 64 * 0.6),
    ]
    coordinates = [inverse.transform(x, y) for x, y in projected]
    pd.DataFrame(
        {
            "tree_id": ["keep", "reject"],
            "longitude": [value[0] for value in coordinates],
            "latitude": [value[1] for value in coordinates],
            "split_eligible": [True, True],
            "split": ["train", "train"],
            "dbh_log1p": [2.0, 1.5],
            "genus_id": [1, 1],
            "species_id": [2, 2],
            "dbh_eligible": [True, True],
            "genus_eligible": [True, True],
            "species_eligible": [True, True],
        }
    ).to_parquet(inventory_dir / "inventory.parquet", index=False)

    raster_path = tmp_path / "feedback-image.tif"
    image = np.full((4, 256, 256), 50, dtype=np.uint8)
    image[3] = 10
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
    feedback_dir = tmp_path / "qa" / "registration" / raster_path.stem
    feedback_dir.mkdir(parents=True)
    feedback_path = feedback_dir / "training-feedback.json"
    feedback_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset": config.dataset,
                "source_raster_name": raster_path.name,
                "registration": {"east_m": 1.2, "north_m": 0.0},
                "exclusions": [{"tree_id": "reject", "split": "train", "reason": "not-tree"}],
                "point_corrections": [
                    {
                        "tree_id": "keep",
                        "split": "train",
                        "east_m": 2.4,
                        "north_m": 0.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = build_chips(config, raster_path)

    manifest = pd.read_parquet(summary["manifest"])
    with np.load(Path(summary["manifest"]).parent / manifest.iloc[0]["path"]) as chip:
        assert chip["center"][64, 66] == 1
        assert chip["center"][32, 33] == 0
        assert chip["detection_mask"][32, 33] == 0
    assert summary["feedback_excluded_points"] == 1
    assert summary["feedback_point_corrected_points"] == 1
    assert summary["registration_correction_m"] == {"east": 1.2, "north": 0.0}

import json
from datetime import date
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

    # A post-flight planting at an existing target must neither become a label
    # nor collide with (and discard) that real target.
    config.imagery.planting_date_cutoff = date(2022, 5, 19)
    inventory = pd.read_parquet(inventory_dir / "inventory.parquet")
    inventory["plant_date"] = None
    later = inventory.iloc[[0]].copy()
    later["tree_id"] = "post-flight-tree"
    later["plant_date"] = "2022-05-20"
    pd.concat([inventory, later], ignore_index=True).to_parquet(
        inventory_dir / "inventory.parquet", index=False
    )

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
    assert summary["post_imagery_planting_excluded_points"] == 1
    assert summary["planting_date_cutoff"] == "2022-05-19"
    assert pd.read_parquet(summary["post_imagery_planting_exclusions"]).tree_id.tolist() == ["post-flight-tree"]

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
    local = json.loads(Path(external_summary["local_normalization"]).read_text(encoding="utf-8"))
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
                "region_overrides": [
                    {
                        "region_id": "protect-background-tree",
                        "mode": "protect",
                        "splits": ["train"],
                        "anchor_longitude": coordinates[0][0],
                        "anchor_latitude": coordinates[0][1],
                        "east_m": 36.0,
                        "north_m": -36.0,
                        "radius_m": 6.0,
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
        assert chip["detection_mask"][94, 94] == 0
    assert summary["feedback_excluded_points"] == 1
    assert summary["feedback_point_corrected_points"] == 1
    assert summary["feedback_mask_regions"] == 1
    assert summary["feedback_region_chip_intersections"] == 1
    assert summary["registration_correction_m"] == {"east": 1.2, "north": 0.0}

    # A manually added crown creates one center, not a circle of positive or
    # ignored pixels, and must not inherit the global inventory offset.
    from urban_tree_ml.confirmed_trees import confirmed_tree_rows
    crown = dict(region_id="new-crown", mode="confirmed-tree", splits=["train"],
                 anchor_longitude=coordinates[0][0], anchor_latitude=coordinates[0][1],
                 east_m=48.0, north_m=-48.0, radius_m=4.2)
    config.split.guard_m = 0
    for seed in range(100):
        config.seed = seed
        if not confirmed_tree_rows([crown], config, "EPSG:32610").empty:
            break
    payload = json.loads(feedback_path.read_text())
    payload["region_overrides"].append(crown)
    feedback_path.write_text(json.dumps(payload))
    added_summary = build_chips(config, raster_path)
    labels = pd.read_parquet(Path(added_summary["manifest"]).parent / "labels.parquet")
    added = labels[labels.tree_id == "manual-new-crown"].iloc[0]
    assert added.crown_radius_m == 4.2
    assert pd.isna(added.species_id) and pd.isna(added.genus_id) and pd.isna(added.dbh_log1p)
    assert added_summary["feedback_mask_regions"] == 1
    with np.load(Path(added_summary["manifest"]).parent / manifest.iloc[0]["path"]) as chip:
        assert chip["center"][int(added.output_y), int(added.output_x)] == 1
    assert added.pixel_col == pytest.approx(128 + 48 / .6)


def test_cleared_validation_chip_is_retained_without_positive_targets(tmp_path):
    test_build_chips_applies_finalized_registration_feedback(tmp_path)
    config = load_config(Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml")
    config.paths.root = tmp_path
    raster_path = tmp_path / "feedback-image.tif"
    with rasterio.open(raster_path) as source:
        image, profile = source.read(), source.profile
        transform = source.transform
    profile["width"] = 512
    with rasterio.open(raster_path, "w", **profile) as target:
        target.write(np.concatenate([image, image], axis=2))
    path = tmp_path / "inventory/ussfo/inventory.parquet"
    frame = pd.read_parquet(path)
    extra = frame.iloc[:1].copy()
    extra["tree_id"] = "cleared-validation"
    extra["split"] = "validation"
    x, y = transform * (320, 64)
    lon, lat = Transformer.from_crs("EPSG:32610", "EPSG:4326", always_xy=True).transform(x, y)
    extra["longitude"], extra["latitude"] = lon, lat
    pd.concat([frame, extra], ignore_index=True).to_parquet(path, index=False)
    feedback_path = tmp_path / "qa/registration/feedback-image/training-feedback.json"
    feedback = json.loads(feedback_path.read_text())
    feedback["exclusions"].append(
        {"tree_id": "cleared-validation", "split": "validation", "reason": "not-tree"}
    )
    feedback_path.write_text(json.dumps(feedback))
    summary = build_chips(config, raster_path, output_dataset="with-empty-validation")
    manifest = pd.read_parquet(summary["manifest"])
    empty = manifest[manifest.split == "validation"].iloc[0]
    assert empty.tree_count == 0
    assert empty.feedback_ignored_count == 1
    with np.load(Path(summary["manifest"]).parent / empty.path) as chip:
        assert not chip["center"].any()
        assert not chip["species_mask"].any()
    labels = pd.read_parquet(summary["labels"])
    assert not (labels.split == "validation").any()

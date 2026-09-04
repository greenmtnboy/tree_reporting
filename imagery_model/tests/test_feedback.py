import json
from pathlib import Path

import pytest

from urban_tree_ml.config import load_config
from urban_tree_ml.feedback import (
    finalize_registration_feedback,
    load_persisted_reviews,
    persist_review_payload,
)


def _write_review_manifest(review_dir: Path, raster: Path) -> None:
    review_dir.mkdir(parents=True)
    samples = [
        {"sample_id": "train-aligned", "tree_id": "a", "split": "train"},
        {"sample_id": "train-offset", "tree_id": "b", "split": "train"},
        {"sample_id": "validation-offset", "tree_id": "c", "split": "validation"},
        {"sample_id": "train-bad", "tree_id": "d", "split": "train"},
        {"sample_id": "validation-uncertain", "tree_id": "e", "split": "validation"},
        {"sample_id": "test-offset", "tree_id": "f", "split": "test"},
    ]
    (review_dir / "manifest.json").write_text(
        json.dumps(
            {
                "metadata": {
                    "review_id": "fixture-review",
                    "source_raster": str(raster),
                },
                "samples": samples,
                "scenes": [
                    {
                        "scene_id": "scene-train",
                        "sample_ids": ["train-aligned", "train-offset", "train-bad"],
                    },
                    {
                        "scene_id": "scene-validation",
                        "sample_ids": ["validation-offset", "validation-uncertain"],
                    },
                    {"scene_id": "scene-test", "sample_ids": ["test-offset"]},
                ],
            }
        ),
        encoding="utf-8",
    )


def test_finalize_uses_training_offsets_and_emits_explicit_exclusions(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml")
    config.paths.root = tmp_path / "artifacts"
    raster = tmp_path / "tile.tif"
    review_dir = tmp_path / "review"
    _write_review_manifest(review_dir, raster)
    persist_review_payload(
        review_dir,
        {
            "metadata": {"review_id": "fixture-review"},
            "reviews": {
                "train-aligned": {
                    "status": "aligned",
                    "east_m": 50,
                    "north_m": 50,
                },
                "train-offset": {"status": "offset", "east_m": 2, "north_m": 4},
                "validation-offset": {"status": "offset", "east_m": 100, "north_m": 100},
                "train-bad": {"status": "not-tree"},
                "validation-uncertain": {"status": "uncertain"},
                "test-offset": {"status": "offset", "east_m": -100, "north_m": -100},
            },
            "scene_reviews": {
                "scene-train": {
                    "done": True,
                    "completed_at": "2026-09-03T15:00:00+00:00",
                }
            },
        },
    )

    result = finalize_registration_feedback(
        config,
        raster,
        review_dir=review_dir,
        minimum_training_reviews=2,
    )

    assert result["registration_status"] == "applied"
    assert result["correction_m"] == {"east": 1.0, "north": 2.0}
    assert result["ignored_test_reviews"] == 1
    feedback = json.loads(Path(result["feedback"]).read_text(encoding="utf-8"))
    assert {entry["tree_id"] for entry in feedback["exclusions"]} == {"d", "e"}
    assert feedback["point_corrections"] == [
        {
            "east_m": 2.0,
            "north_m": 4.0,
            "sample_id": "train-offset",
            "split": "train",
            "tree_id": "b",
        },
        {
            "east_m": 100.0,
            "north_m": 100.0,
            "sample_id": "validation-offset",
            "split": "validation",
            "tree_id": "c",
        },
    ]
    assert result["point_corrected_points"] == 2
    assert result["completed_scenes"] == 1
    assert load_persisted_reviews(review_dir)["scene_reviews"] == {
        "scene-train": {
            "done": True,
            "completed_at": "2026-09-03T15:00:00+00:00",
        }
    }
    assert feedback["reviews"]["completed_scenes"] == 1
    assert feedback["registration"]["validation_residual"]["east_median"] == 99.0


def test_persist_rejects_completion_for_an_unknown_scene(tmp_path: Path) -> None:
    raster = tmp_path / "tile.tif"
    review_dir = tmp_path / "review"
    _write_review_manifest(review_dir, raster)

    with pytest.raises(ValueError, match="unknown scene"):
        persist_review_payload(
            review_dir,
            {"scene_reviews": {"missing-scene": {"done": True}}},
        )


def test_legacy_review_save_preserves_scene_completion(tmp_path: Path) -> None:
    raster = tmp_path / "tile.tif"
    review_dir = tmp_path / "review"
    _write_review_manifest(review_dir, raster)
    persist_review_payload(
        review_dir,
        {"scene_reviews": {"scene-train": {"done": True}}},
    )

    persist_review_payload(
        review_dir,
        {"reviews": {"train-aligned": {"status": "aligned"}}},
    )

    assert load_persisted_reviews(review_dir)["scene_reviews"] == {
        "scene-train": {"done": True}
    }


def test_finalize_rejects_offset_verdict_without_a_clicked_location(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml")
    raster = tmp_path / "tile.tif"
    review_dir = tmp_path / "review"
    _write_review_manifest(review_dir, raster)
    persist_review_payload(
        review_dir,
        {"reviews": {"train-offset": {"status": "offset"}}},
    )

    with pytest.raises(ValueError, match="clicked location"):
        finalize_registration_feedback(
            config,
            raster,
            review_dir=review_dir,
            minimum_training_reviews=1,
        )


def test_stale_reviews_are_not_applied_to_a_regenerated_review(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml")
    raster = tmp_path / "tile.tif"
    review_dir = tmp_path / "review"
    _write_review_manifest(review_dir, raster)
    persist_review_payload(
        review_dir,
        {"reviews": {"train-aligned": {"status": "aligned"}}},
    )
    manifest_path = review_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["metadata"]["review_id"] = "regenerated-review"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert load_persisted_reviews(review_dir)["reviews"] == {}
    with pytest.raises(ValueError, match="do not match"):
        finalize_registration_feedback(
            config,
            raster,
            review_dir=review_dir,
            minimum_training_reviews=1,
        )

import json
from pathlib import Path

import pytest

from urban_tree_ml.config import load_config
from urban_tree_ml.feedback import (
    finalize_registration_feedback,
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
    assert feedback["registration"]["validation_residual"]["east_median"] == 99.0


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

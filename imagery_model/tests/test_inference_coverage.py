import pandas as pd
import pytest

from urban_tree_ml.evaluation import inference_coverage


def test_coverage_records_zero_detections_and_excludes_test():
    manifest = pd.DataFrame({"chip_id": ["a", "b", "sealed"],
                             "split": ["train", "train", "test"]})
    predictions = pd.DataFrame({"chip_id": ["a", "a"]})
    result = inference_coverage(manifest, "train", ["b", "a"], predictions)
    assert result["chips"] == [{"chip_id": "a", "prediction_count": 2},
                               {"chip_id": "b", "prediction_count": 0}]
    empty = inference_coverage(manifest, "train", ["a", "b"], predictions.iloc[:0])
    assert all(c["prediction_count"] == 0 for c in empty["chips"])


@pytest.mark.parametrize("processed", [["a"], ["a", "a", "b"], ["a", "b", "test"]])
def test_coverage_rejects_missing_duplicate_or_unexpected_chips(processed):
    manifest = pd.DataFrame({"chip_id": ["a", "b"], "split": ["train", "train"]})
    with pytest.raises(ValueError, match="coverage"):
        inference_coverage(manifest, "train", processed, pd.DataFrame({"chip_id": []}))


def test_failed_training_inference_does_not_complete_run(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace
    from urban_tree_ml.config import load_config
    from urban_tree_ml.joint import train_and_evaluate

    configs = [load_config("configs/sf_boston_vocab_v2_sf.yaml"),
               load_config("configs/sf_boston_vocab_v2_boston.yaml")]
    for config in configs:
        config.paths.root = tmp_path
    calls = []

    def evaluate(config, **kwargs):
        calls.append(kwargs["cohort"])
        if kwargs["cohort"] == "train-usbos":
            raise ValueError("Incomplete inference coverage")

    monkeypatch.setitem(sys.modules, "urban_tree_ml.training", SimpleNamespace(
        run_training=lambda config: {"best_checkpoint": "best.ckpt"}))
    monkeypatch.setitem(sys.modules, "urban_tree_ml.evaluation", SimpleNamespace(
        run_evaluation=evaluate))
    with pytest.raises(ValueError, match="coverage"):
        train_and_evaluate(configs, "joint", tmp_path)
    assert calls == ["validation", "validation-usbos", "train-ussfo", "train-usbos"]
    assert (tmp_path / "training-result.json").exists()
    assert not (tmp_path / "COMPLETE").exists()

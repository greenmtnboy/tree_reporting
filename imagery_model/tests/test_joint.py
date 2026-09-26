import json

import pandas as pd
import pytest


def test_training_failure_never_evaluates_or_marks_complete(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace
    from urban_tree_ml.config import load_config
    from urban_tree_ml.joint import train_and_evaluate

    def fail(config):
        raise FloatingPointError("Nonfinite validation losses")

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid training must not be evaluated as a successful run")

    monkeypatch.setitem(sys.modules, "urban_tree_ml.training", SimpleNamespace(run_training=fail))
    monkeypatch.setitem(sys.modules, "urban_tree_ml.evaluation",
                        SimpleNamespace(run_evaluation=forbidden))
    with pytest.raises(FloatingPointError):
        train_and_evaluate([load_config("configs/sf_boston_vocab_v2_sf.yaml")], "frozen", tmp_path)
    assert not (tmp_path / "COMPLETE").exists()

from urban_tree_ml.joint import combine_manifests


def test_warm_start_checks_hash_and_taxonomy(monkeypatch, tmp_path):
    import hashlib
    import sys
    from types import SimpleNamespace
    from urban_tree_ml.config import load_config, ReferenceConfig
    from urban_tree_ml.joint import train_and_evaluate

    config = load_config("configs/sf_boston_vocab_v2_sf.yaml")
    config.paths.root = tmp_path
    config.experiment = "new"
    parent = tmp_path / "runs/parent"
    checkpoint = parent / "checkpoints/035.ckpt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"checkpoint")
    taxonomy = parent / "evaluation/validation/taxonomy.json"
    taxonomy.parent.mkdir(parents=True)
    taxonomy.write_text('{"species": ["oak"], "genera": ["Quercus"]}')
    config.reference = ReferenceConfig(taxonomy_path=taxonomy, normalization_path=taxonomy)
    inputs = tmp_path / "run-inputs/new"
    inputs.mkdir(parents=True)
    warm = {"checkpoint": checkpoint.relative_to(tmp_path).as_posix(),
            "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest()}
    (inputs / "warm-start.json").write_text(json.dumps(warm))
    calls = []
    def train(config, **kwargs):
        calls.append(kwargs)
        return {"best_checkpoint": "new.ckpt"}
    monkeypatch.setitem(sys.modules, "urban_tree_ml.training", SimpleNamespace(run_training=train))
    monkeypatch.setitem(sys.modules, "urban_tree_ml.evaluation",
                        SimpleNamespace(run_evaluation=lambda *a, **kw: None))
    output = tmp_path / "output"
    output.mkdir()
    train_and_evaluate([config], "new", output)
    assert calls == [{"initial_checkpoint": str(checkpoint)}]
    checkpoint.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="hash mismatch"):
        train_and_evaluate([config], "new", output)


def test_training_evaluates_validation_then_all_train_never_test(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace

    from urban_tree_ml.config import load_config
    from urban_tree_ml.joint import train_and_evaluate

    configs = [load_config("configs/sf_boston_vocab_v2_sf.yaml"),
               load_config("configs/sf_boston_vocab_v2_boston.yaml")]
    calls = []

    def train(config):
        assert config.dataset == "frozen-original"
        return {"best_checkpoint": "best.ckpt"}

    monkeypatch.setitem(sys.modules, "urban_tree_ml.training",
                        SimpleNamespace(run_training=train))
    monkeypatch.setitem(sys.modules, "urban_tree_ml.evaluation",
                        SimpleNamespace(run_evaluation=lambda *a, **kw: calls.append(kw)))
    train_and_evaluate(configs, "frozen-original", tmp_path)
    assert [c["split"] for c in calls] == ["validation", "validation", "train", "train"]
    assert [c["cohort"] for c in calls] == [
        "validation", "validation-usbos", "train-ussfo", "train-usbos"]
    assert all(c["checkpoint_path"] == "best.ckpt" for c in calls)
    assert (tmp_path / "COMPLETE").exists()


def test_joint_preserves_city_identity_and_sealed_splits(tmp_path):
    sources = {}
    for city in ("ussfo", "usbos"):
        directory = tmp_path / city
        directory.mkdir()
        pd.DataFrame([
            {"chip_id": str(i), "split": split, "path": f"{split}/{i}.npz"}
            for i, split in enumerate(("train", "validation", "test"))
        ]).to_parquet(directory / "chips.parquet")
        (directory / "normalization.json").write_text(
            json.dumps({"mean": [0.5], "std": [0.2]})
        )
        sources[city] = directory
    destination = tmp_path / "joint"
    summary = combine_manifests(sources, destination)
    rows = pd.read_parquet(destination / "chips.parquet")
    assert rows.chip_id.is_unique
    assert list(rows[rows.split == "train"].chip_id) == ["ussfo:0", "usbos:0"]
    assert list(rows[rows.split == "test"].source_chip_id) == ["2", "2"]
    assert rows.iloc[0].path == "../ussfo/train/0.npz"
    assert summary["cities"]["usbos"]["validation"] == 1
    (sources["usbos"] / "normalization.json").write_text(
        json.dumps({"mean": [0.7], "std": [0.2]})
    )
    with pytest.raises(ValueError, match="identical normalization"):
        combine_manifests(sources, destination)

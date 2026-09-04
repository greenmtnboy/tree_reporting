from pathlib import Path

import pytest

from urban_tree_ml.config import load_config


def test_checked_in_config_loads_with_local_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TREE_ML_DATA_ROOT", raising=False)
    config_path = Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml"
    config = load_config(config_path)

    assert config.inventory.city == "USSFO"
    assert config.dataset == "sf-naip-rgbn-species-v2"
    assert config.targets.collision_policy == "discard"
    assert config.model.input_channels == len(config.imagery.bands) == 4
    assert config.paths.root == (Path(__file__).parents[1] / "artifacts").resolve()
    assert config.paths.annotations == (Path(__file__).parents[1] / "annotations").resolve()


def test_environment_overrides_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TREE_ML_DATA_ROOT", str(tmp_path))
    config_path = Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml"

    assert load_config(config_path).paths.root == tmp_path


def test_environment_overrides_annotations_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TREE_ML_ANNOTATIONS_ROOT", str(tmp_path))
    config_path = Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml"

    assert load_config(config_path).paths.annotations == tmp_path


def test_smoke_config_reuses_the_dataset_but_has_an_isolated_run() -> None:
    config_dir = Path(__file__).parents[1] / "configs"
    baseline = load_config(config_dir / "sf_naip_baseline.yaml")
    smoke = load_config(config_dir / "sf_naip_smoke.yaml")

    assert smoke.dataset == baseline.dataset
    assert smoke.experiment != baseline.experiment
    assert smoke.training.accelerator == "gpu"
    assert smoke.training.epochs == 3
    assert smoke.training.batch_size == 8


def test_augmented_config_reuses_labels_and_enables_dihedral_transforms() -> None:
    config_dir = Path(__file__).parents[1] / "configs"
    baseline = load_config(config_dir / "sf_naip_baseline.yaml")
    augmented = load_config(config_dir / "sf_naip_augmented.yaml")

    assert augmented.dataset == baseline.dataset
    assert augmented.experiment != baseline.experiment
    assert augmented.training.random_dihedral
    assert not baseline.training.random_dihedral


def test_citywide_config_uses_an_isolated_dataset_and_vrt() -> None:
    config_dir = Path(__file__).parents[1] / "configs"
    baseline = load_config(config_dir / "sf_naip_baseline.yaml")
    citywide = load_config(config_dir / "sf_naip_citywide.yaml")

    assert citywide.dataset != baseline.dataset
    assert citywide.experiment != baseline.experiment
    assert citywide.imagery.local_raster is not None
    assert citywide.imagery.local_raster.name == "ussfo-2022-mosaic.vrt"
    assert citywide.training.random_dihedral

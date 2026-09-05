from pathlib import Path

import pytest

from urban_tree_ml.config import load_config
from urban_tree_ml.evaluation import run_evaluation


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


def test_curated_config_changes_only_experiment_identity() -> None:
    config_dir = Path(__file__).parents[1] / "configs"
    citywide = load_config(config_dir / "sf_naip_citywide.yaml")
    curated = load_config(config_dir / "sf_naip_citywide_curated.yaml")

    assert curated.experiment != citywide.experiment
    curated_values = curated.model_dump()
    citywide_values = citywide.model_dump()
    curated_values.pop("experiment")
    citywide_values.pop("experiment")
    assert curated_values == citywide_values


def test_boston_external_config_reuses_sf_model_inputs_without_mixing_datasets() -> None:
    config_dir = Path(__file__).parents[1] / "configs"
    sf = load_config(config_dir / "sf_naip_citywide_curated.yaml")
    boston = load_config(config_dir / "boston_naip_external.yaml")

    assert boston.inventory.city == "USBOS"
    assert boston.split.projected_crs == "EPSG:32619"
    assert boston.dataset != sf.dataset
    assert boston.imagery.datetime == "2023-01-01/2023-12-31"
    assert len(boston.imagery.item_ids) == 8
    assert boston.reference is not None
    assert boston.reference.taxonomy_path == (
        boston.paths.root / "inventory" / "ussfo" / "taxonomy.json"
    )
    assert boston.reference.normalization_path == (
        boston.paths.root
        / "chips"
        / "sf-naip-rgbn-species-citywide-v1"
        / "normalization.json"
    )


def test_evaluation_rejects_unsafe_cohort_names_before_loading_torch(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml")

    with pytest.raises(ValueError, match="evaluation cohort"):
        run_evaluation(config, tmp_path / "missing.ckpt", cohort="Boston/validation")

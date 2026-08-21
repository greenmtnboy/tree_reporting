from pathlib import Path

import pytest

from urban_tree_ml.config import load_config


def test_checked_in_config_loads_with_local_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TREE_ML_DATA_ROOT", raising=False)
    config_path = Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml"
    config = load_config(config_path)

    assert config.inventory.city == "USSFO"
    assert config.model.input_channels == len(config.imagery.bands) == 4
    assert config.paths.root == (Path(__file__).parents[1] / "artifacts").resolve()


def test_environment_overrides_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TREE_ML_DATA_ROOT", str(tmp_path))
    config_path = Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml"

    assert load_config(config_path).paths.root == tmp_path

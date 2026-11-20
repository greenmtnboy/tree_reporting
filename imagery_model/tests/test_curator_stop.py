import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("curator_control", Path(__file__).parents[1] / "curator_control.py")
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


def test_stop_refuses_unrecognized_listener(tmp_path):
    with pytest.raises(RuntimeError, match="unrecognized"):
        control.stop(8000, tmp_path, tmp_path, lambda port: "occupied or unresponsive")
    control.stop(8000, tmp_path, tmp_path, lambda port: "stopped")


def test_busy_curator_keeps_control_channel(tmp_path, monkeypatch):
    monkeypatch.setenv("TREE_ML_DATA_ROOT", str(tmp_path))
    path = control.control_path(tmp_path, 8000)
    path.parent.mkdir(parents=True)
    def busy(*args):
        raise RuntimeError("snapshot pending")
    monkeypatch.setattr(control, "idle", busy)
    with control.stop_channel(path), pytest.raises(RuntimeError, match="pending"):
        control.stop(8000, tmp_path, tmp_path, lambda port: "ready")
    assert not list(path.parent.iterdir())

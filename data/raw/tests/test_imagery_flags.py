"""The imagery partitions are opt-in by manifest: `satellite`, `predicted` and
`predicted_staged_with` in raw/{code}/city.toml generate SATELLITE_DATA_SOURCES,
PREDICTED_DATA_SOURCES and PREDICTED_STAGED_WITH.  Whether a flagged city's
model has the other half is test_satellite_wiring.py / test_predicted_wiring.py.
"""

import sys
from pathlib import Path

import pytest

RAW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW / "tools"))
sys.path.insert(0, str(RAW))

import city_manifest  # noqa: E402
from shared.ingest import PREDICTED_DATA_SOURCES, PREDICTED_STAGED_WITH, SATELLITE_DATA_SOURCES  # noqa: E402


def test_the_registries_are_the_flags():
    cities = city_manifest.load_all()
    assert set(SATELLITE_DATA_SOURCES) == {c.code for c in cities if c.satellite}
    assert set(PREDICTED_DATA_SOURCES) == {c.code for c in cities if c.predicted}
    assert PREDICTED_STAGED_WITH == {c.code: c.predicted_staged_with for c in cities if c.predicted_staged_with}


def manifest(tmp_path: Path, code: str, extra: str) -> Path:
    """A copy of DC's manifest under *code* with *extra* top-level keys."""
    text = (RAW / "uswas" / "city.toml").read_text(encoding="utf-8")
    text = text.replace('code = "USWAS"', f'code = "{code}"')
    text = "\n".join(line for line in text.split("\n") if not line.startswith(("satellite =", "predicted")))
    i = text.index("\n\n[") + 1
    path = tmp_path / code.lower() / "city.toml"
    path.parent.mkdir(parents=True)
    path.write_text(text[:i] + extra + text[i:], encoding="utf-8")
    return path


def test_a_flag_is_a_boolean(tmp_path):
    with pytest.raises(ValueError, match="predicted is true or false"):
        city_manifest.load(manifest(tmp_path, "XXAAA", 'predicted = "yes"\n'))


def test_staged_with_needs_predicted(tmp_path):
    with pytest.raises(ValueError, match="needs predicted = true"):
        city_manifest.load(manifest(tmp_path, "XXAAA", 'predicted_staged_with = "XXBBB"\n'))


def test_staged_with_names_a_city_that_stages_its_own_run(tmp_path):
    manifest(tmp_path, "XXAAA", 'predicted = true\npredicted_staged_with = "XXBBB"\n')
    manifest(tmp_path, "XXBBB", "")
    with pytest.raises(ValueError, match="not a city that stages its own predictions"):
        city_manifest.load_all(tmp_path)
    (tmp_path / "xxbbb" / "city.toml").unlink()
    (tmp_path / "xxbbb").rmdir()
    manifest(tmp_path, "XXBBB", "predicted = true\n")
    assert {c.code: c.predicted_staged_with for c in city_manifest.load_all(tmp_path)} == {
        "XXAAA": "XXBBB", "XXBBB": None,
    }

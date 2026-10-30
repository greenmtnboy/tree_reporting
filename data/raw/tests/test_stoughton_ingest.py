"""Stoughton's site-history layer: which rows are trees, and what the ingest
reads from them.  Pinned without a network fetch."""

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, RAW_DIR / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stoughton = _load("stoughton_tree_info", "ussto/stoughton_tree_info.py")

# 2018-05-15 UTC, in Esri epoch milliseconds.
REPLANTED_MS = 1526393429919


def _feature(**attrs) -> dict:
    base = {
        "GlobalID": "7cf125a5-69e3-4880-b4e2-ff620f67de6d",
        "Genus": "Catalpa",
        "SPECIES": "speciosa",
        "Variety": None,
        "Common": "Northern Catalpa",
        "DBH": 2,
        "TreeStatus": "Tree",
        "TreeReplaced": None,
        "TreeReplaceDate": None,
    }
    base.update(attrs)
    return {"attributes": base, "geometry": {"x": -89.2228, "y": 42.9116}}


def test_status_decides_and_history_does_not():
    """A replanted site keeps `TreeRemoved = 'Removed'` for the old tree; the
    status is what says a tree stands there now."""
    rows = [
        _feature(TreeStatus="Tree", TreeRemoved="Removed"),
        _feature(GlobalID="b", TreeStatus="Planting Site"),
        _feature(GlobalID="c", TreeStatus="Stump"),
        _feature(GlobalID="d", TreeStatus="Empty"),
        _feature(GlobalID="e", TreeStatus=None),
    ]
    table = stoughton.transform(rows)
    assert table.column("tree_id").to_pylist() == ["sto-7cf125a5-69e3-4880-b4e2-ff620f67de6d"]


def test_an_unseen_status_raises():
    with pytest.raises(RuntimeError, match="Removed Tree"):
        stoughton.transform([_feature(TreeStatus="Removed Tree")])


def test_genus_and_epithet_columns_join():
    row = stoughton.transform([_feature(Genus="Ginkgo ", SPECIES="biloba")]).to_pylist()[0]
    assert row["species"] == "Ginkgo biloba"


def test_a_not_applicable_epithet_is_the_genus():
    row = stoughton.transform([_feature(Genus="Ulmus", SPECIES="N/A")]).to_pylist()[0]
    assert row["species"] == "Ulmus"


def test_variety_is_the_cultivar_trimmed():
    row = stoughton.transform([_feature(Variety="Exclamation ")]).to_pylist()[0]
    assert row["cultivar"] == "Exclamation"


def test_a_replaced_site_dates_its_tree():
    row = stoughton.transform(
        [_feature(TreeReplaced="Replaced", TreeReplaceDate=REPLANTED_MS)]
    ).to_pylist()[0]
    assert row["plant_date"] == date(2018, 5, 15)


def test_a_site_awaiting_replacement_has_no_plant_date():
    row = stoughton.transform(
        [_feature(TreeReplaced="To Be Replaced", TreeReplaceDate=REPLANTED_MS)]
    ).to_pylist()[0]
    assert row["plant_date"] is None

"""Two portals stamp a default planting date on the rows they have none for.

Edmonton writes 1990-06-01 on 54% of its inventory and Melbourne writes
1900-01-01 on a third of its dated trees; each is a real-looking date on a
population whose diameters span the whole city.  An age model reads either
as an age, so each city's ingest publishes it as null.  A year-only record
(Boston's 1994-01-01, Edmonton's 2014-06-01) is an age and stays.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa
import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))


def load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, RAW_DIR / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def edmonton():
    return load("edmonton_tree_info", "caedm/edmonton_tree_info.py")


@pytest.fixture(scope="module")
def melbourne():
    return load("melbourne_tree_info", "aumel/melbourne_tree_info.py")


def test_edmonton_publishes_its_stamped_default_as_null(edmonton):
    assert edmonton.PLACEHOLDER_PLANT_DATE == date(1990, 6, 1)
    assert edmonton.parse_plant_date("1990-06-01T00:00:00.000") is None
    assert edmonton.parse_plant_date("2014-06-01T00:00:00.000") == date(2014, 6, 1)  # a real cohort, June 1 too
    assert edmonton.parse_plant_date("1990-06-02") == date(1990, 6, 2)
    assert edmonton.parse_plant_date("") is None
    assert edmonton.parse_plant_date("not a date") is None


def test_melbourne_publishes_its_stamped_default_as_null(melbourne):
    assert melbourne.PLACEHOLDER_PLANT_DATE == date(1900, 1, 1)
    raw = pa.table(
        {
            "com_id": pa.array([1, 2, 3, 4], type=pa.int64()),
            "genus": pa.array(["Ulmus", "Ulmus", "Platanus", "Ulmus"]),
            "species": pa.array(["procera", "procera", "acerifolia", "procera"]),
            "common_name": pa.array(["English elm"] * 4),
            "dbh": pa.array([80.0, 35.0, 20.0, None], type=pa.float64()),
            "date_planted": pa.array([date(1890, 1, 1), date(1900, 1, 1), date(2012, 1, 1), None], type=pa.date32()),
            "latitude": pa.array([-37.81] * 4, type=pa.float64()),
            "longitude": pa.array([144.96] * 4, type=pa.float64()),
        }
    )
    table = melbourne.transform(raw)
    assert table.column("plant_date").to_pylist() == [date(1890, 1, 1), None, date(2012, 1, 1), None]
    assert table.column("tree_id").to_pylist() == ["mel-1", "mel-2", "mel-3", "mel-4"]

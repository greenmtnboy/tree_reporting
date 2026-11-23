"""Arnold Arboretum's published unit metadata and stable Boston row mapping."""

import importlib.util
from pathlib import Path

import pytest


RAW_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def arb():
    spec = importlib.util.spec_from_file_location(
        "arboretum_tree_info", RAW_DIR / "usbos" / "arboretum_tree_info.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "value, code, label, expected",
    [
        (84.5, "C", "centimeters", 33.2677165354),
        (62.20000076, " c ", "Centimeters ", 24.4881892756),
        (2.54, "cm", None, 1.0),
        (12.0, "I", "inches", 12.0),
        (1.29999995, "M", "meters", 51.1811003937),
        (0.0, "C", "centimeters", 0.0),
        (2.54, None, "centimetres", 1.0),
    ],
)
def test_normalizes_documented_units(arb, value, code, label, expected):
    assert arb.convert_dbh(value, code, label) == pytest.approx(expected)


@pytest.mark.parametrize(
    "value, code, label",
    [
        (None, "C", "centimeters"),
        (84.5, None, None),
        (84.5, " ", " "),
        (84.5, "X", None),
        (84.5, None, "unknown"),
        (84.5, "X", "centimeters"),
        (84.5, "C", "unknown"),
        (84.5, "I", "centimeters"),
    ],
)
def test_does_not_guess_missing_unknown_or_conflicting_units(arb, value, code, label):
    assert arb.convert_dbh(value, code, label) is None


def test_fetch_and_table_use_unit_label_without_changing_tree_ids(arb, monkeypatch):
    records = [
        {
            "OBJECTID": 214,
            "SCIENTIFIC_NAME": "Quercus rubra",
            "LATITUDE": 42.29499669,
            "LONGITUDE": -71.12307239,
            "DBH_NUM": 84.5,
            "DBH_UNIT_CODE": "C",
            "DBH_UNIT": "centimeters",
        },
        {
            "OBJECTID": 64375,
            "SCIENTIFIC_NAME": "Acer micranthum",
            "DBH_NUM": 1.29999995,
            "DBH_UNIT_CODE": "M",
            "DBH_UNIT": "meters",
        },
        {
            "OBJECTID": 141,
            "DBH_NUM": 62.20000076,
            "DBH_UNIT_CODE": "I",
            "DBH_UNIT": "centimeters",
        },
    ]

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"features": [{"attributes": row} for row in records]}

    def get(_url, *, params, timeout):
        assert {"DBH_NUM", "DBH_UNIT_CODE", "DBH_UNIT"} <= set(
            params["outFields"].split(",")
        )
        return Response()

    monkeypatch.setattr(arb.requests, "get", get)
    rows = arb.build_table(arb.fetch_page(0)).to_pylist()
    assert [row["tree_id"] for row in rows] == ["arb-214", "arb-64375", "arb-141"]
    assert rows[0]["species"] == "Quercus rubra"
    assert rows[0]["diameter_at_breast_height"] == pytest.approx(33.2677165354)
    assert rows[1]["diameter_at_breast_height"] == pytest.approx(51.1811003937)
    assert rows[2]["diameter_at_breast_height"] is None

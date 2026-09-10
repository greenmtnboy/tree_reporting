"""Amsterdam's diameter classes, in both formats the portal has published.

The v1 strings ("0 t/m 20 cm") were keyed in a dict, so when the portal
switched to "0,1 tot 0,2 m." every tree lost its diameter and nothing said
so: 70 of 300,891 published rows carried one.  The parser now reads either,
and the ingest refuses to publish when the format moves again.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))


@pytest.fixture(scope="module")
def ams():
    spec = importlib.util.spec_from_file_location(
        "amsterdam_tree_info", RAW_DIR / "nlams" / "amsterdam_tree_info.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "value, cm",
    [
        ("0,1 tot 0,2 m.", 15.0),
        ("0,2 tot 0,3 m.", 25.0),
        ("0,3 tot 0,5 m.", 40.0),
        ("0,5 tot 1 m.", 75.0),
        ("1,0 tot 1,5 m.", 125.0),
        ("0 t/m 20 cm", 10.0),
        ("21 t/m 40 cm", 30.5),
        ("141 t/m 160 cm", 150.5),
        ("> 160 cm", 160.0),
        ("> 1,5 m.", 150.0),
    ],
)
def test_parses_both_formats_to_inches(ams, value, cm):
    assert math.isclose(ams.parse_dbh(value), cm / 2.54, rel_tol=1e-9)


@pytest.mark.parametrize("value", [None, "", "  ", "Onbekend", "n.v.t."])
def test_unreadable_classes_are_none(ams, value):
    assert ams.parse_dbh(value) is None


def test_a_published_class_is_a_street_tree_not_a_giant(ams):
    """The 2026 format is metres; reading it as centimetres, or as inches,
    is the bug this file exists for.  A 0.1-0.2 m tree is about 6 inches."""
    assert 5 < ams.parse_dbh("0,1 tot 0,2 m.") < 7


def test_transform_counts_what_it_could_not_parse(ams):
    ams.UNPARSED_CLASSES.clear()
    rows = [
        {"id": 1, "soortnaam": "Tilia cordata", "geometrie": None, "stamdiameterklasse": "0,1 tot 0,2 m."},
        {"id": 2, "soortnaam": "Tilia cordata", "geometrie": None, "stamdiameterklasse": "Onbekend"},
        {"id": 3, "soortnaam": "Tilia cordata", "geometrie": None, "stamdiameterklasse": None},
        {"id": 4, "soortnaam": "Tilia cordata", "geometrie": None, "stamdiameterklasse": "dik"},
    ]
    table = ams.transform(rows)
    assert table.column("diameter_at_breast_height").to_pylist()[1:] == [None, None, None]
    # "Onbekend" is the inventory saying it does not know, and is not counted
    # against the format guard; a string that is neither a class nor a known
    # placeholder is.
    assert ams.UNPARSED_CLASSES == {"dik": 1}
    ams.UNPARSED_CLASSES.clear()
